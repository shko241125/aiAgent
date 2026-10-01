"""RCA 원인 후보 랭킹 엔진 (M1-08 / 2.2).

LLM 에게 원인을 '창작'시키지 않기 위해, 코드가 먼저 근거가 붙은 후보를 뽑고 점수화한다.
LLM(RCAAgent)은 이 후보를 검증·선택·설명한다.
LLM 없이도 Top-k 적중률을 잴 수 있어 회귀 기준선이 된다.

근거 수집 범위: 알람 서비스 + 하위 의존성(downstream, 최대 depth 3)
점수 신호
  1) change          : 이상 시작 직전의 배포/설정 변경 (시간이 가까울수록 ↑)
  2) error_signature : 로그에서 알려진 장애 시그니처 (커넥션 풀, OOM, 인증서 …)
  3) dependency      : 알람 서비스보다 먼저 이상해진 하위 서비스 (장애 전파의 출발점)
  4) resource        : CPU/메모리 포화
  시간 순서 원칙: 원인은 증상보다 먼저 시작한다 — 가장 먼저 이상해진 서비스일수록 원인일 가능성 ↑
"""

import math
import re
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

from aiops.analytics.anomaly.base import AnomalyDetector
from aiops.analytics.anomaly.ensemble import default_detector
from aiops.analytics.logs import summarize_logs
from aiops.domain.models import OpsEvent, utcnow
from aiops.integrations.base import OpsSource

METRICS = ["cpu_usage", "memory_usage", "latency_p95_ms", "error_rate"]
RESOURCE_METRICS = {"cpu_usage", "memory_usage"}

# (정규식, 원인 라벨, 관련 runbook doc_id)
SIGNATURES: list[tuple[str, str, str]] = [
    (
        r"HikariPool|Connection is not available|connection pool",
        "DB 커넥션 풀 고갈",
        "runbook-db-connection-pool",
    ),
    (r"OutOfMemoryError|OOMKilled|heap space", "메모리 부족(OOM)", "runbook-memory-leak-oom"),
    (
        r"x509|certificate has expired|SSLHandshake|PKIX",
        "TLS 인증서 만료/검증 실패",
        "runbook-tls-certificate-expiry",
    ),
    (r"No space left on device", "디스크 가득 참", "runbook-disk-full"),
    (
        r"UnknownHostException|NXDOMAIN|no such host",
        "DNS 이름 해석 실패",
        "runbook-dns-resolution-failure",
    ),
    (r"Lock wait timeout|deadlock", "DB 락 경합", "runbook-db-slow-query-lock"),
    (r"[Rr]edis.*(timeout|timed out)|Command timed out", "Redis 지연", "runbook-redis-latency"),
    (r"rebalanc|consumer lag", "Kafka 컨슈머 지연", "runbook-kafka-consumer-lag"),
]

Kind = Literal["change", "error_signature", "dependency", "resource"]


class ServiceEvidence(BaseModel):
    service: str
    depth: int  # 0 = 알람 서비스
    onset_min_ago: float | None = None  # 지속적 이상이 시작된 시점 (분 전)
    anomalous_metrics: dict[str, float] = Field(default_factory=dict)  # metric -> 최대 점수
    changes: list[OpsEvent] = Field(default_factory=list)
    log_lines: list[str] = Field(default_factory=list)


class RCACandidate(BaseModel):
    service: str
    kind: Kind
    cause: str
    score: float
    evidence: list[str]
    runbook: str | None = None


def ongoing_onset(
    indices: list[int], n: int, min_run: int = 3, min_density: float = 0.6, tail: int = 3
) -> int | None:
    """현재까지 이어지는 이상의 시작 인덱스. 없으면 None.

    알람 시점의 장애는 '지금도 진행 중'이다. 창 앞부분의 일시적 노이즈(계절성 등)를
    원인으로 오인하지 않도록 세 조건을 모두 요구한다:
      1) min_run 개 이상 연속된 구간에서 시작  2) 시작~끝 구간의 이상치 밀도 ≥ min_density
      3) 마지막 tail 개 포인트 안에 이상치 존재
    """
    if not indices or indices[-1] < n - tail:
        return None
    marks = set(indices)
    runs, start, prev = [], None, None
    for i in indices:
        if prev is None or i != prev + 1:
            start = i
        if i - start + 1 == min_run:
            runs.append(start)
        prev = i
    for s in runs:  # 가장 이른 시작점부터 — 밀도 조건을 만족하는 첫 지점
        if sum(1 for i in range(s, n) if i in marks) / (n - s) >= min_density:
            return s
    return None


def match_signatures(lines: list[str]) -> list[tuple[str, str, str]]:
    found = []
    for pattern, label, runbook in SIGNATURES:
        hit = next((ln for ln in lines if re.search(pattern, ln)), None)
        if hit:
            found.append((label, runbook, hit))
    return found


def rank_candidates(evidence: list[ServiceEvidence], top_k: int = 5) -> list[RCACandidate]:
    anomalous = [e for e in evidence if e.onset_min_ago is not None]
    earliest = max((e.onset_min_ago for e in anomalous), default=None)
    alert = next((e for e in evidence if e.depth == 0), None)
    cands: list[RCACandidate] = []

    for e in evidence:
        earliness = (e.onset_min_ago / earliest) if (earliest and e.onset_min_ago) else 0.0
        sigs = match_signatures(e.log_lines)

        # 1) 변경: 이상 시작 전(최대 60분)의 변경일수록 강한 후보
        for ch in e.changes:
            ch_ago = (utcnow() - ch.timestamp).total_seconds() / 60
            if e.onset_min_ago is not None:
                gap = ch_ago - e.onset_min_ago
                if not -1 <= gap <= 60:
                    continue
                score = 0.45 + 0.35 * math.exp(-max(gap, 0) / 20) + (0.15 if sigs else 0)
            elif ch_ago <= 60 and earliest is not None:
                score = 0.2  # 이상 없는 서비스의 변경 — 약한 후보
            else:
                continue
            cause = f"{e.service} 변경({ch.type}: {ch.message})"
            if sigs:
                cause += f" 이후 {sigs[0][0]}"
            cands.append(
                RCACandidate(
                    service=e.service,
                    kind="change",
                    cause=cause,
                    score=score,
                    evidence=[f"{ch_ago:.0f}분 전 {ch.type}", *(f"log: {s[2]}" for s in sigs[:1])],
                    runbook="runbook-5xx-after-deploy" if not sigs else sigs[0][1],
                )
            )

        # 2) 로그 시그니처
        for label, runbook, line in sigs:
            score = 0.55 + 0.25 * earliness if e.onset_min_ago is not None else 0.4
            cands.append(
                RCACandidate(
                    service=e.service,
                    kind="error_signature",
                    cause=f"{e.service}: {label}",
                    score=score,
                    evidence=[f"log: {line}"],
                    runbook=runbook,
                )
            )

        if e.onset_min_ago is None:
            continue

        # 3) 하위 의존성이 알람 서비스보다 먼저 이상해짐 → 전파의 출발점
        if (
            e.depth > 0
            and alert
            and (alert.onset_min_ago is None or e.onset_min_ago >= alert.onset_min_ago - 0.5)
        ):
            cands.append(
                RCACandidate(
                    service=e.service,
                    kind="dependency",
                    cause=f"하위 의존성 {e.service} 장애 전파",
                    score=0.4 + 0.3 * earliness + 0.05 * e.depth,
                    evidence=[
                        f"{e.onset_min_ago:.0f}분 전부터 이상: {sorted(e.anomalous_metrics)}"
                    ],
                )
            )

        # 4) 자원 포화 (시그니처가 설명하지 못할 때 보조 후보)
        res = {m: s for m, s in e.anomalous_metrics.items() if m in RESOURCE_METRICS}
        if res:
            metric = max(res, key=res.get)
            deeper_first = any(
                o.depth > e.depth and o.onset_min_ago and o.onset_min_ago > e.onset_min_ago
                for o in anomalous
            )
            score = (0.3 + 0.3 * earliness) * (0.6 if deeper_first else 1.0)
            cands.append(
                RCACandidate(
                    service=e.service,
                    kind="resource",
                    cause=f"{e.service} {metric} 포화",
                    score=score,
                    evidence=[f"{metric} 이상 (max score {res[metric]:.1f})"],
                    runbook="runbook-high-cpu"
                    if metric == "cpu_usage"
                    else "runbook-memory-leak-oom",
                )
            )

    best: dict[tuple[str, str], RCACandidate] = {}
    for c in cands:
        c.score = round(min(c.score, 1.0), 3)
        key = (c.service, c.kind)
        if key not in best or c.score > best[key].score:
            best[key] = c
    return sorted(best.values(), key=lambda c: c.score, reverse=True)[:top_k]


class RCAAnalyzer:
    """데이터 소스에서 근거를 모아 후보를 랭킹한다."""

    def __init__(
        self,
        source: OpsSource,
        detector: AnomalyDetector | None = None,
        window_min: int = 60,
        max_depth: int = 3,
    ) -> None:
        self.source = source
        self.detector = detector or default_detector()
        self.window_min = window_min
        self.max_depth = max_depth

    async def scope(self, service: str) -> list[tuple[str, int]]:
        seen = {service: 0}
        frontier = [service]
        while frontier:
            s = frontier.pop(0)
            if seen[s] >= self.max_depth:
                continue
            for d in (await self.source.dependencies(s)).get("downstream", []):
                if d not in seen:
                    seen[d] = seen[s] + 1
                    frontier.append(d)
        return list(seen.items())

    async def collect(self, service: str) -> list[ServiceEvidence]:
        end = utcnow()
        start = end - timedelta(minutes=self.window_min)
        out = []
        for svc, depth in await self.scope(service):
            ev = ServiceEvidence(service=svc, depth=depth)
            onsets = []
            for metric in METRICS:
                series = await self.source.query_range(svc, metric, start, end)
                anomalies = self.detector.detect(series.values)
                idx = ongoing_onset([a.index for a in anomalies], len(series.values))
                if idx is not None:
                    onsets.append(len(series.values) - idx)  # step 60s → 분 단위
                    ev.anomalous_metrics[metric] = round(max(a.score for a in anomalies), 2)
            ev.onset_min_ago = float(max(onsets)) if onsets else None
            ev.changes = [
                e
                for e in await self.source.list_events(end - timedelta(hours=2), end, svc)
                if e.type in ("deploy", "config")
            ]
            raw = [
                r["message"] for r in await self.source.search(svc, "error", start, end, limit=500)
            ]
            ev.log_lines = summarize_logs(raw)  # Drain 템플릿 (×개수) — M2-04
            out.append(ev)
        return out

    async def analyze(self, service: str, top_k: int = 5) -> list[RCACandidate]:
        return rank_candidates(await self.collect(service), top_k=top_k)
