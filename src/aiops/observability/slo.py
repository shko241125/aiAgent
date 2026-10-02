"""SLO 정의 → Prometheus 기록·알람 규칙 / Grafana 대시보드 생성 (M4-03 / 4.6).

SLO 는 여기 한 곳에만 정의한다. `scripts/gen_observability.py` 가 deploy/ 아래 파일을 생성하고,
테스트가 커밋된 생성물과 비교한다 → 임계치가 코드·알람·대시보드에서 어긋나지 않는다.

알람: Google SRE Workbook 5장 '멀티 윈도우·멀티 번 레이트'.
  번 레이트 = 오류 비율 / 오류 예산(1 - 목표). 1 이면 기간 끝에 예산을 딱 다 쓴다.
  긴 창(예: 1h)이 '충분히 많이 태웠는가' 를, 짧은 창(5m)이 '지금도 타고 있는가' 를 본다
  → 이미 회복된 장애로 계속 울리지 않는다.
"""

from pydantic import BaseModel

from aiops.observability.metrics import LATENCY_BUCKETS


class SLO(BaseModel):
    name: str
    description: str
    objective: float  # 예: 0.995 → 30일 동안 요청의 99.5% 가 '좋음'
    good: str  # 좋은 이벤트 수 rate 의 PromQL ({w} = 창)
    total: str  # 전체 이벤트 수 rate 의 PromQL
    window_days: int = 30

    @property
    def budget(self) -> float:
        return round(1 - self.objective, 6)


LATENCY_THRESHOLD_S = 0.5  # 대화형 API p95 목표 (버킷 경계여야 함)
assert LATENCY_THRESHOLD_S in LATENCY_BUCKETS

SLOS = [
    SLO(
        name="api-availability",
        description="API 요청의 99.5% 는 5xx 가 아니다",
        objective=0.995,
        good='sum(rate(aiops_http_requests_total{code!~"5.."}[{w}]))',
        total="sum(rate(aiops_http_requests_total[{w}]))",
    ),
    SLO(
        name="api-latency",
        description=f"대화형 API 요청의 95% 는 {LATENCY_THRESHOLD_S}s 안에 응답한다 (p95 목표)",
        objective=0.95,
        good="sum(rate(aiops_http_request_duration_seconds_bucket"
        f'{{class="interactive",le="{LATENCY_THRESHOLD_S}"}}[{{w}}]))',
        total='sum(rate(aiops_http_request_duration_seconds_count{class="interactive"}[{w}]))',
    ),
    SLO(
        name="llm-availability",
        description="LLM 호출의 99% 는 성공한다 (fallback 이전 시도 단위)",
        objective=0.99,
        good='sum(rate(aiops_llm_requests_total{outcome="ok"}[{w}]))',
        total="sum(rate(aiops_llm_requests_total[{w}]))",
    ),
]

# (심각도, 긴 창, 짧은 창, 번 레이트) — 30일 예산 기준 SRE Workbook 권장값
BURN_ALERTS = [
    ("page", "1h", "5m", 14.4),  # 1시간에 예산 2% 소모
    ("page", "6h", "30m", 6.0),  # 6시간에 5%
    ("ticket", "1d", "2h", 3.0),  # 하루에 10%
    ("ticket", "3d", "6h", 1.0),  # 사흘에 10%
]
_UNIT_S = {"m": 60, "h": 3600, "d": 86400}
WINDOWS = sorted(
    {w for _, long, short, _ in BURN_ALERTS for w in (long, short)},
    key=lambda w: int(w[:-1]) * _UNIT_S[w[-1]],
)


def _record(slo: SLO, w: str) -> str:
    return f"slo:{slo.name.replace('-', '_')}:error_ratio_rate{w}"


def prometheus_rules() -> dict:
    groups = []
    for slo in SLOS:
        records = [
            {
                "record": _record(slo, w),
                "expr": f"1 - ({slo.good.replace('{w}', w)}) / ({slo.total.replace('{w}', w)})",
            }
            for w in WINDOWS
        ]
        alerts = [
            {
                "alert": f"SLOBurn-{slo.name}-{long}",
                "expr": f"{_record(slo, long)} > {rate * slo.budget:.6g}"
                f" and {_record(slo, short)} > {rate * slo.budget:.6g}",
                "labels": {"severity": sev, "slo": slo.name},
                "annotations": {
                    "summary": f"{slo.name} 오류 예산 소모 속도 {rate}x ({long}/{short})",
                    "description": slo.description,
                },
            }
            for sev, long, short, rate in BURN_ALERTS
        ]
        groups.append({"name": f"slo-{slo.name}", "rules": records + alerts})
    return {"groups": groups}


def _panel(pid: int, title: str, targets: list[tuple[str, str]], x: int, y: int, **extra) -> dict:
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
        "gridPos": {"x": x, "y": y, "w": 12, "h": 8},
        "targets": [
            {"refId": chr(65 + i), "expr": expr, "legendFormat": legend}
            for i, (expr, legend) in enumerate(targets)
        ],
        **extra,
    }


def _threshold(value: float, unit: str) -> dict:
    return {
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {"thresholdsStyle": {"mode": "line"}},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "green", "value": None}, {"color": "red", "value": value}],
                },
            }
        }
    }


def grafana_dashboard() -> dict:
    avail = SLOS[0]
    q = 'histogram_quantile(0.95, sum by (le) (rate(aiops_http_request_duration_seconds_bucket{class="interactive"}[5m])))'  # noqa: E501
    panels = [
        _panel(
            1,
            "요청률 (route 별)",
            [("sum by (route) (rate(aiops_http_requests_total[5m]))", "{{route}}")],
            0,
            0,
            fieldConfig={"defaults": {"unit": "reqps"}},
        ),
        _panel(
            2,
            f"대화형 API p95 지연 (목표 < {LATENCY_THRESHOLD_S}s)",
            [(q, "p95")],
            12,
            0,
            **_threshold(LATENCY_THRESHOLD_S, "s"),
        ),
        _panel(
            3,
            f"5xx 비율 (예산 {avail.budget:.3%})",
            [(f"{_record(avail, '5m')}", "5m"), (f"{_record(avail, '1h')}", "1h")],
            0,
            8,
            **_threshold(avail.budget, "percentunit"),
        ),
        _panel(
            4,
            "SLO 번 레이트 (1h, 1 = 예산을 기간에 맞춰 소모)",
            [(f"{_record(s, '1h')} / {s.budget}", s.name) for s in SLOS],
            12,
            8,
            **_threshold(14.4, "none"),
        ),
        _panel(
            5,
            "LLM 호출 (provider·결과)",
            [
                (
                    "sum by (provider, outcome) (rate(aiops_llm_requests_total[5m]))",
                    "{{provider}} {{outcome}}",
                )
            ],  # noqa: E501
            0,
            16,
        ),
        _panel(
            6,
            "LLM p95 지연",
            [
                (
                    "histogram_quantile(0.95, sum by (le, provider) (rate(aiops_llm_request_duration_seconds_bucket[5m])))",  # noqa: E501
                    "{{provider}}",
                )
            ],
            12,
            16,
            fieldConfig={"defaults": {"unit": "s"}},
        ),
        _panel(
            7,
            "워크플로우 단계 결과",
            [
                (
                    "sum by (step, status) (increase(aiops_workflow_steps_total[1h]))",
                    "{{step}} {{status}}",
                )
            ],  # noqa: E501
            0,
            24,
        ),
        _panel(
            8,
            "승인 결정·조치 결과",
            [
                (
                    "sum by (status) (increase(aiops_approval_decisions_total[1h]))",
                    "승인 {{status}}",
                ),
                (
                    'sum by (type, outcome) (increase(aiops_remediation_actions_total{mode="real"}[1h]))',  # noqa: E501
                    "조치 {{type}} {{outcome}}",
                ),
            ],
            12,
            24,
        ),
    ]
    return {
        "uid": "aiops-slo",
        "title": "AIOps Platform — SLO",
        "schemaVersion": 39,
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "30s",
        "templating": {
            "list": [{"name": "datasource", "type": "datasource", "query": "prometheus"}]
        },
        "panels": panels,
    }
