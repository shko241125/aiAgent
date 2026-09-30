"""RCA 시나리오 평가 (M1-08) — 결정적 후보 랭킹의 Top-k 적중률."""

import json
from pathlib import Path

from pydantic import BaseModel

from aiops.analytics.rca import RCAAnalyzer, RCACandidate
from aiops.integrations.simulated import FaultScenario, SimulatedOpsSource


class ScenarioOutcome(BaseModel):
    id: str
    hit_rank: int | None  # 몇 번째 후보가 정답이었나 (없으면 None)
    top: list[str]


class RCAReport(BaseModel):
    top1: float
    top3: float
    outcomes: list[ScenarioOutcome]


def load_scenarios(path: Path) -> list[FaultScenario]:
    return [FaultScenario.model_validate(s) for s in json.loads(path.read_text(encoding="utf-8"))]


def hit_rank(cands: list[RCACandidate], sc: FaultScenario) -> int | None:
    for i, c in enumerate(cands, 1):
        if any(c.service == a.service and c.kind in a.kinds for a in sc.accept):
            return i
    return None


async def evaluate_rca(scenarios: list[FaultScenario], seed: int = 7) -> RCAReport:
    outcomes = []
    for sc in scenarios:
        source = SimulatedOpsSource(seed=seed)
        source.apply_scenario(sc)
        cands = await RCAAnalyzer(source).analyze(sc.alert_service)
        outcomes.append(
            ScenarioOutcome(
                id=sc.id,
                hit_rank=hit_rank(cands, sc),
                top=[f"{c.service}/{c.kind}:{c.score}" for c in cands[:3]],
            )
        )
    n = len(outcomes) or 1
    return RCAReport(
        top1=round(sum(1 for o in outcomes if o.hit_rank == 1) / n, 3),
        top3=round(sum(1 for o in outcomes if o.hit_rank and o.hit_rank <= 3) / n, 3),
        outcomes=outcomes,
    )
