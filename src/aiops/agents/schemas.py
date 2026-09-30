"""에이전트 구조화 출력 스키마 (M1-01 / 1.1).

에이전트 간 인계는 이 스키마로 이뤄진다. LLMAgent 는 최종 답변의 JSON 을 여기에 검증하고,
위반 시 오류 내용을 알려주며 재요청한다 → 형식 오류가 다음 단계로 전파되지 않게 경계에서 막는다.
"""

from pydantic import BaseModel, Field

from aiops.domain.models import Severity


class DetectionOutput(BaseModel):
    is_incident: bool
    severity: Severity = Severity.WARNING
    affected_services: list[str] = Field(default_factory=list)
    summary: str = ""


class Hypothesis(BaseModel):
    cause: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class RCAOutput(BaseModel):
    root_cause: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    service: str | None = None  # 원인 서비스 (증상 서비스와 다를 수 있음)
    evidence: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)


class PlanStep(BaseModel):
    step: str
    tool: str | None = None
    risk: str = "read"


class RemediationOutput(BaseModel):
    plan: list[PlanStep] = Field(default_factory=list)
    executed: list[str] = Field(default_factory=list)
    pending_approval: list[str] = Field(default_factory=list)
