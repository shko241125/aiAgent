"""Tool Calling Framework (1.5).

- `@tool` 데코레이터로 일반 파이썬 함수를 LLM 도구로 등록 (타입힌트 → JSON Schema 자동 생성)
- 위험도(risk)에 따라 승인 정책(Human-in-the-loop)을 강제
- 호출 인자 검증(pydantic), 타임아웃, 예외 격리를 ToolRegistry 가 일괄 처리
"""

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, get_type_hints

from pydantic import BaseModel, Field, ValidationError, create_model

from aiops.llm.base import ToolCall, ToolSpec

logger = logging.getLogger(__name__)


RUNTIME_PARAM = "runtime"


@dataclass
class ToolRuntime:
    """도구 실행 시 주입되는 호출자 정보. LLM 이 조작할 수 없도록 스키마에서 제외된다.

    함수 시그니처에 `runtime: ToolRuntime` 파라미터가 있으면 자동 주입.
    """

    agent: str
    ctx: Any = None  # AgentContext (순환 import 회피)
    card_id: str | None = None


class ToolRisk(StrEnum):
    READ = "read"  # 조회 전용 — 자동 실행
    WRITE = "write"  # 상태 변경 (재시작, 스케일링 등) — 승인 필요
    DESTRUCTIVE = "destructive"  # 삭제/롤백 등 — 항상 승인 필요


class ToolResult(BaseModel):
    call_id: str
    name: str
    ok: bool
    output: Any = None
    error: str | None = None
    pending_approval: bool = False

    def to_llm_content(self) -> str:
        if self.pending_approval:
            return json.dumps({"status": "PENDING_APPROVAL", "detail": self.error})
        if not self.ok:
            return json.dumps({"status": "ERROR", "error": self.error})
        return json.dumps(self.output, ensure_ascii=False, default=str)


class Tool(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    name: str
    description: str
    func: Callable[..., Any]
    args_model: type[BaseModel]
    risk: ToolRisk = ToolRisk.READ
    timeout_s: float = 30.0
    tags: set[str] = Field(default_factory=set)
    needs_runtime: bool = False

    @property
    def spec(self) -> ToolSpec:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, parameters=schema)

    async def invoke(self, arguments: dict[str, Any], runtime: ToolRuntime | None = None) -> Any:
        args = self.args_model.model_validate(arguments)
        kwargs = args.model_dump()
        if self.needs_runtime:
            kwargs[RUNTIME_PARAM] = runtime
        if inspect.iscoroutinefunction(self.func):
            return await asyncio.wait_for(self.func(**kwargs), timeout=self.timeout_s)
        return await asyncio.wait_for(asyncio.to_thread(self.func, **kwargs), self.timeout_s)


def tool(
    name: str | None = None,
    *,
    description: str | None = None,
    risk: ToolRisk = ToolRisk.READ,
    timeout_s: float = 30.0,
    tags: set[str] | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """함수를 Tool 로 변환. 함수 docstring 첫 단락이 description 이 된다."""

    def decorator(func: Callable[..., Any]) -> Tool:
        hints = get_type_hints(func)
        fields: dict[str, Any] = {}
        params = inspect.signature(func).parameters
        for pname, param in params.items():
            if pname == RUNTIME_PARAM:
                continue
            annotation = hints.get(pname, Any)
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[pname] = (annotation, default)
        args_model = create_model(f"{func.__name__}_args", **fields)
        doc = inspect.getdoc(func) or ""
        return Tool(
            name=name or func.__name__,
            description=description or doc.split("\n\n")[0] or func.__name__,
            func=func,
            args_model=args_model,
            risk=risk,
            timeout_s=timeout_s,
            tags=tags or set(),
            needs_runtime=RUNTIME_PARAM in params,
        )

    return decorator


class ApprovalPolicy:
    """위험도 기반 실행 승인 정책. TODO(2.3): 승인 요청을 Slack/ITSM 으로 보내고 콜백 처리."""

    def __init__(self, auto_approve: bool = False) -> None:
        self.auto_approve = auto_approve

    def requires_approval(self, t: Tool, approved: set[str]) -> bool:
        if t.risk == ToolRisk.READ or t.name in approved:
            return False
        if t.risk == ToolRisk.DESTRUCTIVE:
            return True
        return not self.auto_approve


class ToolRegistry:
    def __init__(self, policy: ApprovalPolicy | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.policy = policy or ApprovalPolicy()

    def register(self, *tools: Tool) -> None:
        for t in tools:
            if t.name in self._tools:
                raise ValueError(f"duplicated tool name: {t.name}")
            self._tools[t.name] = t

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        selected = names if names is not None else list(self._tools)
        return [self._tools[n].spec for n in selected]

    async def invoke(
        self,
        call: ToolCall,
        *,
        approved: set[str] | None = None,
        runtime: ToolRuntime | None = None,
    ) -> ToolResult:
        t = self._tools.get(call.name)
        if t is None:
            return ToolResult(call_id=call.id, name=call.name, ok=False, error="unknown tool")
        if self.policy.requires_approval(t, approved or set()):
            return ToolResult(
                call_id=call.id,
                name=call.name,
                ok=False,
                pending_approval=True,
                error=f"'{t.name}' ({t.risk}) requires human approval",
            )
        try:
            output = await t.invoke(call.arguments, runtime)
            return ToolResult(call_id=call.id, name=call.name, ok=True, output=output)
        except ValidationError as exc:
            return ToolResult(call_id=call.id, name=call.name, ok=False, error=str(exc))
        except TimeoutError:
            return ToolResult(call_id=call.id, name=call.name, ok=False, error="timeout")
        except Exception as exc:  # noqa: BLE001 - 도구 실패가 에이전트 전체를 죽이면 안 된다
            logger.exception("tool %s failed", call.name)
            return ToolResult(call_id=call.id, name=call.name, ok=False, error=repr(exc))
