"""Drain 평가용 합성 로그 (M2-04) — 정답 템플릿을 아는 로그 줄 생성."""

import random
import uuid

TEMPLATES = {
    "pool": "HikariPool-{n} - Connection is not available, request timed out after {ms}ms.",
    "conn": "Connection to {ip}:{port} timed out after {ms}ms",
    "req": "GET /api/orders/{n} completed status={status} duration={ms}ms",
    "user": "User {uuid} logged in from {ip}",
    "gc": "GC pause (G1 Evacuation Pause) young {mb}M->{mb2}M({mb3}M) {sec} secs",
    "oom": "java.lang.OutOfMemoryError: Java heap space in thread pool-{n}-thread-{k}",
    "cert": "x509: certificate has expired or is not yet valid: current time {ts} is after {ts2}",
    "retry": "Retrying request to payment-service attempt={k} backoff={ms}ms",
}


def generate_logs(n: int = 2000, seed: int = 0) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    keys = list(TEMPLATES)
    weights = [30, 15, 25, 10, 8, 4, 3, 5]
    lines, labels = [], []
    for _ in range(n):
        k = rng.choices(keys, weights)[0]
        lines.append(
            TEMPLATES[k].format(
                n=rng.randint(1, 9999),
                ms=rng.randint(1, 30000),
                ip=f"10.0.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
                port=rng.choice([5432, 6379, 443]),
                status=rng.choice([200, 201, 500, 503]),
                uuid=uuid.UUID(int=rng.getrandbits(128)),
                mb=rng.randint(100, 900),
                mb2=rng.randint(10, 99),
                mb3=rng.randint(1000, 4000),
                sec=f"{rng.random():.4f}",
                k=rng.randint(1, 8),
                ts=f"2026-10-01T0{rng.randint(0, 9)}:00:00Z",
                ts2="2026-09-30T00:00:00Z",
            )
        )
        labels.append(k)
    return lines, labels
