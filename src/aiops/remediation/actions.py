"""조치 모델 (M3-02 / 2.3) — 실행 가능한 조치 종류는 코드가 정한 화이트리스트뿐이다."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aiops.domain.models import new_id, utcnow


class ActionType(StrEnum):
    RESTART = "restart"  # 롤링 재시작
    SCALE = "scale"  # replica 조정
    ROLLBACK = "rollback"  # 직전 리비전으로 되돌림
    MANUAL = "manual"  # 자동화 불가 — 사람이 수행 (인증서 교체 등)


RISK = {
    ActionType.RESTART: "write",
    ActionType.SCALE: "write",
    ActionType.ROLLBACK: "destructive",
    ActionType.MANUAL: "none",
}


class RemediationAction(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    service: str
    type: ActionType
    namespace: str = "default"
    params: dict[str, Any] = Field(default_factory=dict)  # replicas, to_revision …
    reason: str = ""
    runbook: str | None = None

    @property
    def risk(self) -> str:
        return RISK[self.type]

    def describe(self) -> str:
        extra = f" {self.params}" if self.params else ""
        return f"{self.type} {self.namespace}/{self.service}{extra} — {self.reason}"


class ActionResult(BaseModel):
    action_id: str
    ok: bool
    dry_run: bool
    detail: str = ""
    revert: RemediationAction | None = None  # 이 조치를 되돌리는 조치 (불가능하면 None)
    executed_at: datetime = Field(default_factory=utcnow)
