"""M3-03 플레이북 · 효과 검증 · 되돌림·에스컬레이션 · 평가."""

from pathlib import Path

from aiops.analytics.rca import RCACandidate
from aiops.evals.rca import load_scenarios
from aiops.evals.remediation import evaluate_remediation
from aiops.integrations.simulated import Fault, FaultScenario, SimulatedOpsSource
from aiops.remediation.actions import ActionType, RemediationAction
from aiops.remediation.executors import SimulatedExecutor
from aiops.remediation.guardrails import GuardrailPolicy, Guardrails
from aiops.remediation.playbook import plan_actions
from aiops.remediation.runner import remediate
from aiops.remediation.service import RemediationService

ROOT = Path(__file__).resolve().parents[2]


async def no_sleep(_):
    return None


def cand(service, kind, cause, score=0.8):
    return RCACandidate(service=service, kind=kind, cause=cause, score=score, evidence=[])


def test_playbook_mapping_and_dedupe():
    plan = plan_actions(
        [
            cand(
                "order-service", "change", "order-service 변경(deploy: v2) 이후 DB 커넥션 풀 고갈"
            ),
            cand("order-service", "error_signature", "order-service: DB 커넥션 풀 고갈"),
            cand(
                "payment-service", "error_signature", "payment-service: TLS 인증서 만료/검증 실패"
            ),
            cand("order-service", "change", "중복 변경 후보"),
        ]
    )
    assert [(a.service, a.type) for a in plan] == [
        ("order-service", ActionType.ROLLBACK),
        ("order-service", ActionType.RESTART),
        ("payment-service", ActionType.MANUAL),
    ]
    scale = plan_actions([cand("api", "resource", "api cpu_usage 포화")])[0]
    assert scale.type == ActionType.SCALE and scale.params == {"factor": 2}


def _setup(fixed_by):
    src = SimulatedOpsSource(seed=2)
    src.apply_scenario(
        FaultScenario(
            id="x",
            alert_service="api-gateway",
            faults=[
                Fault(
                    service="api-gateway",
                    metric="cpu_usage",
                    magnitude=2.5,
                    onset_min_ago=20,
                    fixed_by=fixed_by,
                )
            ],
        )
    )
    ex = SimulatedExecutor(src)
    return src, ex, RemediationService(ex, Guardrails(GuardrailPolicy(cooldown_s=0)))


async def test_recovered_after_right_action():
    src, ex, svc = _setup(["scale"])
    action = RemediationAction(service="api-gateway", type=ActionType.SCALE, params={"factor": 2})
    out = await remediate(
        svc, src, action, approved_by="sre", verify_services=["api-gateway"], sleep=no_sleep
    )
    assert out.status == "recovered" and ex.replicas["api-gateway"] == 6  # 3 × 2
    assert not out.escalate


async def test_not_recovered_reverts_and_escalates():
    src, ex, svc = _setup(["restart"])  # scale 로는 안 고쳐지는 장애
    action = RemediationAction(service="api-gateway", type=ActionType.SCALE, params={"factor": 2})
    out = await remediate(
        svc,
        src,
        action,
        approved_by="sre",
        verify_services=["api-gateway"],
        verify_attempts=2,
        sleep=no_sleep,
    )
    assert out.status == "not_recovered" and out.escalate
    assert out.reverted.ok and ex.replicas["api-gateway"] == 3  # 원상 복구 (쿨다운 면제)
    assert out.verification.remaining == {"api-gateway": ["cpu_usage"]}


async def test_manual_is_escalated_without_state_change():
    src, ex, svc = _setup([])
    out = await remediate(
        svc,
        src,
        RemediationAction(service="api-gateway", type=ActionType.MANUAL, reason="인증서"),
        approved_by="sre",
        verify_services=["api-gateway"],
        sleep=no_sleep,
    )
    assert out.status == "manual" and out.escalate and ex.executed == []


async def test_remediation_scenarios_baseline():
    """기준선 (시나리오가 플레이북과 함께 작성됨 — 실 장애 이력으로 재검증 필요)."""
    r = await evaluate_remediation(load_scenarios(ROOT / "data/eval/rca_scenarios.json"))
    assert r.plan_accuracy == 1.0 and r.recovery_rate == 1.0 and r.safe_manual == 1.0
