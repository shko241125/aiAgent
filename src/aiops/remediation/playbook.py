"""조치 플레이북 (M3-03 / 2.3) — RCA 후보 → 조치. 결정적이며 근거(runbook)가 붙는다.

원칙
- 원인이 '변경'이면 되돌린다(rollback) — 가장 빠르고 확실한 복구 수단 (runbook-5xx-after-deploy)
- 자동화가 위험하거나 불가능한 원인(인증서·디스크·DB 락·DNS·Redis)은 MANUAL — 사람에게 넘긴다
- 같은 서비스·같은 조치는 한 번만, 점수 높은 후보 순서대로 1차 조치·대안을 만든다
"""

from aiops.analytics.rca import RCACandidate
from aiops.remediation.actions import ActionType, RemediationAction

SIGNATURE_ACTIONS: dict[str, tuple[ActionType, str]] = {
    "DB 커넥션 풀 고갈": (ActionType.RESTART, "누수된 커넥션 회수 (임시 완화)"),
    "메모리 부족(OOM)": (ActionType.RESTART, "힙 회수 (힙 덤프 확보 후)"),
    "Kafka 컨슈머 지연": (ActionType.SCALE, "컨슈머 처리 병렬도 증가"),
    "TLS 인증서 만료/검증 실패": (ActionType.MANUAL, "인증서 갱신·교체"),
    "디스크 가득 참": (ActionType.MANUAL, "원인 파일 확인 후 정리·볼륨 확장"),
    "DNS 이름 해석 실패": (ActionType.MANUAL, "CoreDNS 상태·설정 확인"),
    "DB 락 경합": (ActionType.MANUAL, "락 보유 트랜잭션 확인 후 판단"),
    "Redis 지연": (ActionType.MANUAL, "O(N) 명령·big key 확인"),
}


def _signature_label(c: RCACandidate) -> str:
    return c.cause.split(": ", 1)[-1] if c.kind == "error_signature" else ""


def plan_actions(
    candidates: list[RCACandidate], namespace: str = "default", max_actions: int = 3
) -> list[RemediationAction]:
    plan: list[RemediationAction] = []
    seen: set[tuple[str, str]] = set()

    def add(service: str, t: ActionType, reason: str, runbook: str | None, **params) -> None:
        if (service, t) not in seen and len(plan) < max_actions:
            seen.add((service, t))
            plan.append(
                RemediationAction(
                    service=service,
                    type=t,
                    namespace=namespace,
                    params=params,
                    reason=reason,
                    runbook=runbook,
                )
            )

    for c in candidates:
        if c.kind == "change":
            add(
                c.service,
                ActionType.ROLLBACK,
                f"이상 직전 변경 되돌림 — {c.cause}",
                "runbook-5xx-after-deploy",
            )
        elif c.kind == "error_signature":
            t, why = SIGNATURE_ACTIONS.get(_signature_label(c), (ActionType.MANUAL, "원인 확인"))
            params = {"factor": 1.5} if t == ActionType.SCALE else {}
            add(c.service, t, f"{why} — {c.cause}", c.runbook, **params)
        elif c.kind == "resource":
            if "cpu" in c.cause:
                add(c.service, ActionType.SCALE, f"부하 분산 — {c.cause}", c.runbook, factor=2)
            else:
                add(c.service, ActionType.RESTART, f"메모리 회수 — {c.cause}", c.runbook)
        elif c.kind == "dependency":
            add(c.service, ActionType.MANUAL, f"하위 의존성 조사 — {c.cause}", c.runbook)
    return plan
