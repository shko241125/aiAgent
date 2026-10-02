"""부하 테스트 (M4-04 / 4.6) — 요청 혼합을 동시 실행해 p50/p95/p99·에러율·RPS 를 재고 SLO 로 판정.

    python scripts/loadtest.py                               # 프로세스 내(ASGI) 기준선
    python scripts/loadtest.py --llm-latency-ms 300 --cache  # 느린 LLM + 응답 캐시 효과
    python scripts/loadtest.py --url http://host:8000 --api-key KEY   # 배포된 서버 (M4-08)

프로세스 내 측정은 네트워크·실 DB·실 LLM 이 없는 '코드 경로' 기준선이다. SLO 판정의 최종 근거는
배포 환경 측정(M4-08)이어야 한다.
"""

import argparse
import asyncio
import json
import logging
import random
import statistics
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path

import httpx

from aiops.core.config import Settings
from aiops.llm.cache import ResponseCache
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.llm.router import LLMRouter
from aiops.main import create_app
from aiops.observability.slo import LATENCY_THRESHOLD_S, SLOS

QUESTIONS = [
    "HikariPool 커넥션 고갈 조치 방법",
    "order-service 5xx 급증 원인",
    "배포 후 지연 증가 롤백 절차",
    "메모리 누수 OOMKilled 대응",
    "결제 서비스 타임아웃",
]
# (가중치, 메서드, 경로, 본문 생성기)
MIX = [
    (3, "GET", "/api/v1/incidents", None),
    (2, "GET", "/api/v1/approvals", None),
    (1, "GET", "/ready", None),
    (
        2,
        "POST",
        "/api/v1/analytics/anomalies",
        lambda r: {"values": [10 + r.random() for _ in range(59)] + [80.0]},
    ),
    (2, "POST", "/api/v1/rag/search", lambda r: {"query": r.choice(QUESTIONS), "k": 3}),
    (1, "POST", "/api/v1/rag/answer", lambda r: {"query": r.choice(QUESTIONS), "k": 3}),
]


class SlowFakeLLM(FakeLLMProvider):
    """실 LLM 지연을 흉내 낸다 (캐시 효과를 보기 위함)."""

    def __init__(self, latency_s: float) -> None:
        super().__init__()
        self.latency_s = latency_s

    async def chat(self, messages, **kw):
        await asyncio.sleep(self.latency_s)
        return await super().chat(messages, **kw)


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def summarize(samples: list[tuple[str, int, float]], elapsed: float) -> dict:
    out: dict = {"requests": len(samples), "rps": round(len(samples) / elapsed, 1), "routes": {}}
    by_cls: dict[str, list[float]] = {}
    for path, _, sec in samples:
        cls = "llm" if path == "/api/v1/rag/answer" else "interactive"
        by_cls.setdefault(cls, []).append(sec)
        out["routes"].setdefault(path, []).append(sec)
    out["routes"] = {
        p: {"n": len(v), "p95_ms": round(pct(v, 0.95) * 1000, 1)} for p, v in out["routes"].items()
    }
    for cls, v in by_cls.items():
        out[cls] = {
            "n": len(v),
            "p50_ms": round(statistics.median(v) * 1000, 1),
            "p95_ms": round(pct(v, 0.95) * 1000, 1),
            "p99_ms": round(pct(v, 0.99) * 1000, 1),
        }
    errors = sum(1 for _, code, _ in samples if code >= 500 or code == 0)
    out["error_rate"] = round(errors / len(samples), 5)
    budget = next(s for s in SLOS if s.name == "api-availability").budget
    p95 = out.get("interactive", {}).get("p95_ms", 0) / 1000
    out["slo"] = {
        "interactive_p95": {"target_s": LATENCY_THRESHOLD_S, "pass": p95 < LATENCY_THRESHOLD_S},
        "availability": {"error_budget": budget, "pass": out["error_rate"] <= budget},
    }
    return out


async def run(client: httpx.AsyncClient, n: int, concurrency: int, seed: int) -> dict:
    rng = random.Random(seed)
    weights = [w for w, *_ in MIX]
    plan = [rng.choices(MIX, weights)[0] for _ in range(n)]
    queue: asyncio.Queue = asyncio.Queue()
    for item in plan:
        queue.put_nowait(item)
    samples: list[tuple[str, int, float]] = []

    async def worker(wid: int) -> None:
        r = random.Random(seed + wid)
        while not queue.empty():
            _, method, path, body = queue.get_nowait()
            t0 = time.perf_counter()
            try:
                resp = await client.request(method, path, json=body(r) if body else None)
                code = resp.status_code
            except httpx.HTTPError:
                code = 0
            samples.append((path, code, time.perf_counter() - t0))

    t0 = time.perf_counter()
    await asyncio.gather(*(worker(i) for i in range(concurrency)))
    return summarize(samples, time.perf_counter() - t0)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--llm-latency-ms", type=float, default=0)
    ap.add_argument("--cache", action="store_true", help="LLM 응답 캐시 켜기 (프로세스 내 전용)")
    ap.add_argument("--url", help="배포된 서버 주소 (없으면 프로세스 내 ASGI)")
    ap.add_argument("--api-key")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    # aiops.main 을 불러오면 앱 로깅(stdout)이 켜진다 — 결과 JSON 만 깨끗이 나오도록 낮춘다
    logging.getLogger().setLevel(logging.WARNING)
    for name in ("httpx", "aiops", "aiops.access"):
        logging.getLogger(name).setLevel(logging.WARNING)

    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    async with AsyncExitStack() as stack:
        cache = fake = None
        if args.url:
            client = httpx.AsyncClient(base_url=args.url, headers=headers, timeout=60)
        else:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            settings = Settings(
                database_url=f"sqlite+aiosqlite:///{tmp}/load.db",
                approval_sweep_interval_s=0,
                log_level="WARNING",
            )
            cache = ResponseCache() if args.cache else None
            fake = SlowFakeLLM(args.llm_latency_ms / 1000)
            llm = LLMRouter({"fake": fake}, default="fake", cache=cache)
            app = create_app(settings, llm=llm)
            await stack.enter_async_context(app.router.lifespan_context(app))
            transport = httpx.ASGITransport(app=app)
            client = httpx.AsyncClient(transport=transport, base_url="http://load", headers=headers)
        await stack.enter_async_context(client)
        result = await run(client, args.requests, args.concurrency, args.seed)
    result["target"] = args.url or "in-process"
    result["config"] = {
        "concurrency": args.concurrency,
        "llm_latency_ms": args.llm_latency_ms,
        "cache": args.cache,
    }
    if fake is not None:  # 실제로 LLM 까지 간 호출 수 (캐시 적중·동시 요청 합치기 제외)
        result["llm_provider_calls"] = len(fake.calls)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0 if all(v["pass"] for v in result["slo"].values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
