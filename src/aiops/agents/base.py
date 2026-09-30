"""AI Agent 기본 설계 (1.1).

BaseAgent : 모든 에이전트의 계약 (이름·설명·run)
LLMAgent  : LLM + 도구 호출 루프(ReAct 스타일)를 제공하는 표준 구현.
            특화 에이전트는 프롬프트/도구/전후처리 훅만 바꿔 끼운다 (Template Method 패턴).

    build_input() → [LLM ↔ Tool 반복 (max_steps)] → post_process() → AgentResult
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from aiops.agents.context import AgentContext
from aiops.agents.memory.base import ConversationMemory
from aiops.agents.tools.base import ToolRegistry, ToolResult
from aiops.llm.base import ChatMessage, LLMProvider
from aiops.prompts.registry import PromptRegistry

logger = logging.getLogger(__name__)


class AgentTask(BaseModel):
    instruction: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    agent: str
    success: bool = True
    output: str = ""
    data: dict[str, Any] = Field(default_factory=dict)  # 구조화 결과 (JSON 파싱 결과 등)
    tool_results: list[ToolResult] = Field(default_factory=list)
    steps: int = 0
    pending_approvals: list[str] = Field(default_factory=list)


class BaseAgent(ABC):
    name: str
    description: str

    @abstractmethod
    async def run(self, task: AgentTask, ctx: AgentContext) -> AgentResult: ...


def extract_json(text: str | None) -> dict[str, Any]:
    """LLM 출력에서 마지막 JSON 객체를 추출 (```json 블록 또는 raw)."""
    if not text:
        return {}
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not candidates:
        start = text.rfind("{")
        while start != -1:
            try:
                return json.loads(text[start:])
            except json.JSONDecodeError:
                start = text.rfind("{", 0, start)
        return {}
    try:
        return json.loads(candidates[-1])
    except json.JSONDecodeError:
        return {}


class LLMAgent(BaseAgent):
    prompt_name: str = ""
    tool_names: list[str] = []

    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        prompts: PromptRegistry,
        *,
        max_steps: int = 8,
        memory_window: int = 20,
        temperature: float = 0.0,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.prompts = prompts
        self.max_steps = max_steps
        self.memory_window = memory_window
        self.temperature = temperature

    # ---- 확장 훅 -------------------------------------------------------------
    async def build_input(self, task: AgentTask, ctx: AgentContext) -> dict[str, Any]:
        """프롬프트 템플릿 변수. 서브클래스가 blackboard/분석 결과를 주입한다."""
        return {"input": task.instruction, **task.inputs}

    async def post_process(self, result: AgentResult, ctx: AgentContext) -> AgentResult:
        """결과를 blackboard 에 기록하는 등 후처리."""
        ctx.blackboard.write(f"{self.name}.output", result.output, author=self.name)
        if result.data:
            ctx.blackboard.write(f"{self.name}.data", result.data, author=self.name)
        return result

    # ---- 실행 루프 -----------------------------------------------------------
    async def run(self, task: AgentTask, ctx: AgentContext) -> AgentResult:
        variables = await self.build_input(task, ctx)
        prompt = self.prompts.render(self.prompt_name, **variables)
        memory = ConversationMemory(window=self.memory_window)
        memory.add(ChatMessage.system(prompt.system), ChatMessage.user(prompt.user))
        available = [n for n in self.tool_names if n in self.tools.names()]
        specs = self.tools.specs(available) if available else None

        result = AgentResult(agent=self.name)
        for step in range(1, self.max_steps + 1):
            result.steps = step
            resp = await self.llm.chat(memory.messages(), tools=specs, temperature=self.temperature)
            ctx.log(self.name, "llm_call", step=step, tool_calls=len(resp.tool_calls))
            memory.add(ChatMessage.assistant(resp.content, resp.tool_calls))

            if not resp.tool_calls:
                result.output = resp.content or ""
                result.data = extract_json(resp.content)
                break

            for call in resp.tool_calls:
                if call.name not in available:
                    tr = ToolResult(
                        call_id=call.id,
                        name=call.name,
                        ok=False,
                        error="tool not allowed for this agent",
                    )
                else:
                    tr = await self.tools.invoke(call, approved=ctx.approved_tools)
                ctx.log(
                    self.name,
                    "tool_call",
                    tool=call.name,
                    args=call.arguments,
                    ok=tr.ok,
                    pending=tr.pending_approval,
                )
                result.tool_results.append(tr)
                if tr.pending_approval:
                    result.pending_approvals.append(call.name)
                memory.add(ChatMessage.tool(call.id, call.name, tr.to_llm_content()))
        else:
            result.success = False
            result.output = f"max_steps({self.max_steps}) exceeded"
            logger.warning("%s: %s", self.name, result.output)

        ctx.log(self.name, "result", success=result.success)
        return await self.post_process(result, ctx)
