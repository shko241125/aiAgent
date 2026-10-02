"""가드레일 (M3-02 / 2.3) — 실행기 앞에서 강제되는 안전 정책.

LLM 의 잘못된 계획이든 사람의 실수든 모든 조치가 같은 관문을 지난다.
"""

import fnmatch
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from aiops.domain.models import utcnow
from aiops.remediation.actions import ActionType, RemediationAction


class GuardrailViolation(RuntimeError):
    pass


class GuardrailPolicy(BaseModel):
    allowed_namespaces: list[str] = Field(default_factory=lambda: ["default"])
    denied_services: list[str] = Field(default_factory=lambda: ["*-db", "*database*"])  # glob
    allowed_types: list[ActionType] = Field(
        default_factory=lambda: [ActionType.RESTART, ActionType.SCALE, ActionType.ROLLBACK]
    )
    max_actions_per_hour: int = 3  # 서비스당
    cooldown_s: int = 300  # 같은 서비스 연속 조치 사이 최소 간격
    max_scale_factor: float = 2.0  # 한 번에 현재의 몇 배까지
    min_replicas: int = 1
    max_replicas: int = 20


class Guardrails:
    def __init__(
        self, policy: GuardrailPolicy | None = None, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self.policy = policy or GuardrailPolicy()
        self.clock = clock
        self.history: dict[str, list[datetime]] = defaultdict(list)

    def check(self, action: RemediationAction, current_replicas: int | None = None) -> None:
        p, now = self.policy, self.clock()
        if action.type not in p.allowed_types:
            raise GuardrailViolation(f"허용되지 않은 조치 종류: {action.type}")
        if action.namespace not in p.allowed_namespaces:
            raise GuardrailViolation(f"허용되지 않은 네임스페이스: {action.namespace}")
        if any(fnmatch.fnmatch(action.service, pat) for pat in p.denied_services):
            raise GuardrailViolation(f"자동 조치 금지 대상: {action.service}")
        recent = [t for t in self.history[action.service] if now - t < timedelta(hours=1)]
        if len(recent) >= p.max_actions_per_hour:
            raise GuardrailViolation(
                f"{action.service} 시간당 조치 한도 초과({len(recent)}/{p.max_actions_per_hour})"
            )
        if recent and (now - max(recent)).total_seconds() < p.cooldown_s:
            raise GuardrailViolation(f"{action.service} 쿨다운 중 ({p.cooldown_s}s)")
        if action.type == ActionType.SCALE:
            target = int(action.params.get("replicas", 0))
            if not p.min_replicas <= target <= p.max_replicas:
                raise GuardrailViolation(
                    f"replicas {target} 범위 밖 [{p.min_replicas},{p.max_replicas}]"
                )
            if current_replicas and target > current_replicas * p.max_scale_factor:
                raise GuardrailViolation(
                    f"스케일 배율 초과: {current_replicas}→{target} (최대 ×{p.max_scale_factor})"
                )

    def record(self, action: RemediationAction) -> None:
        self.history[action.service].append(self.clock())
