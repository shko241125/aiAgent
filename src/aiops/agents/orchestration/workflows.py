"""사전 정의된 Agent Workflow (1.3).

incident_response:
    detection ─▶ rca ─▶ remediation ─▶ incident ─▶ report
                  (장애가 아니라고 판단되면 rca 이후 단계는 SKIPPED)
"""

from aiops.agents.context import AgentContext
from aiops.agents.orchestration.orchestrator import Orchestrator
from aiops.workflow.engine import Workflow, WorkflowRun


def _is_incident(run: WorkflowRun) -> bool:
    det = run.steps["detect"].output
    # LLM 이 명시적으로 is_incident=false 라고 판단한 경우에만 중단 (불확실하면 계속 진행)
    return not (det is not None and det.data.get("is_incident") is False)


def incident_response_workflow(
    orch: Orchestrator, ctx: AgentContext, with_remediation: bool = True
) -> Workflow:
    steps = [
        orch.agent_step("detect", "detection", "알람을 분석해 장애 여부를 판단하라", ctx),
        orch.agent_step(
            "rca", "rca", "근본 원인을 분석하라", ctx, depends_on=["detect"], condition=_is_incident
        ),
    ]
    last = "rca"
    if with_remediation:
        steps.append(
            orch.agent_step(
                "remediate", "remediation", "복구 조치를 계획·실행하라", ctx, depends_on=["rca"]
            )
        )
        last = "remediate"
    steps += [
        orch.agent_step(
            "incident",
            "incident",
            "인시던트 기록과 상황 공유 메시지를 작성하라",
            ctx,
            depends_on=[last],
        ),
        orch.agent_step(
            "report",
            "report",
            "이번 인시던트의 포스트모템 보고서를 작성하라",
            ctx,
            depends_on=["incident"],
        ),
    ]
    return Workflow(
        name="incident_response",
        steps=steps,
        description="알람 → 탐지 → RCA → 조치 → 인시던트 기록 → 보고서",
    )
