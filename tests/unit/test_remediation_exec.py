"""M3-02 조치 실행기 · 가드레일."""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from aiops.analytics.rca import RCAAnalyzer
from aiops.domain.models import utcnow
from aiops.integrations.simulated import Fault, FaultScenario, SimulatedOpsSource
from aiops.remediation.actions import ActionType, RemediationAction
from aiops.remediation.executors import KubernetesExecutor, SimulatedExecutor
from aiops.remediation.guardrails import GuardrailPolicy, Guardrails, GuardrailViolation
from aiops.remediation.service import RemediationService


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now


def act(t, svc="order-service", **params):
    return RemediationAction(service=svc, type=t, params=params)


def test_guardrails():
    clock = Clock()
    g = Guardrails(GuardrailPolicy(max_actions_per_hour=2, cooldown_s=60), clock=clock)
    with pytest.raises(GuardrailViolation, match="금지"):
        g.check(act(ActionType.RESTART, svc="order-db"))
    with pytest.raises(GuardrailViolation, match="네임스페이스"):
        g.check(act(ActionType.RESTART).model_copy(update={"namespace": "kube-system"}))
    with pytest.raises(GuardrailViolation, match="허용되지 않은 조치"):
        g.check(act(ActionType.MANUAL))
    with pytest.raises(GuardrailViolation, match="배율"):
        g.check(act(ActionType.SCALE, replicas=9), current_replicas=3)
    g.check(act(ActionType.SCALE, replicas=6), current_replicas=3)
    g.record(act(ActionType.RESTART))
    with pytest.raises(GuardrailViolation, match="쿨다운"):
        g.check(act(ActionType.RESTART))
    clock.now += timedelta(minutes=2)
    g.record(act(ActionType.RESTART))
    clock.now += timedelta(minutes=2)
    with pytest.raises(GuardrailViolation, match="한도"):
        g.check(act(ActionType.RESTART))


def _k8s(seen):
    deployment = {
        "metadata": {
            "name": "order-service",
            "annotations": {"deployment.kubernetes.io/revision": "3"},
        },
        "spec": {"selector": {"matchLabels": {"app": "order"}}},
    }

    def rs(rev, image):
        return {
            "metadata": {
                "annotations": {"deployment.kubernetes.io/revision": str(rev)},
                "ownerReferences": [{"kind": "Deployment", "name": "order-service"}],
            },
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "order", "pod-template-hash": f"h{rev}"}},
                    "spec": {"containers": [{"image": image}]},
                }
            },
        }

    def handler(req: httpx.Request):
        body = json.loads(req.content) if req.content else None
        seen.append(
            (
                req.method,
                req.url.path,
                parse_qs(urlparse(str(req.url)).query),
                req.headers.get("content-type"),
                body,
            )
        )
        if req.url.path.endswith("/scale") and req.method == "GET":
            return httpx.Response(200, json={"spec": {"replicas": 3}})
        if req.url.path.endswith("/replicasets"):
            return httpx.Response(
                200, json={"items": [rs(1, "app:v1"), rs(2, "app:v2"), rs(3, "app:v3")]}
            )
        if req.method == "GET":
            return httpx.Response(200, json=deployment)
        return httpx.Response(200, json={})

    return httpx.AsyncClient(base_url="https://k8s", transport=httpx.MockTransport(handler))


async def test_kubernetes_restart_scale_rollback_contract():
    seen = []
    k = KubernetesExecutor("https://k8s", http_client=_k8s(seen))
    r = await k.execute(act(ActionType.RESTART), dry_run=True)
    method, path, query, ctype, body = seen[-1]
    assert (method, path) == ("PATCH", "/apis/apps/v1/namespaces/default/deployments/order-service")
    assert query == {"dryRun": ["All"]} and ctype == "application/strategic-merge-patch+json"
    assert (
        "kubectl.kubernetes.io/restartedAt" in body["spec"]["template"]["metadata"]["annotations"]
    )
    assert r.ok and r.dry_run

    r = await k.execute(act(ActionType.SCALE, replicas=5))
    assert seen[-1][3] == "application/merge-patch+json" and seen[-1][4] == {
        "spec": {"replicas": 5}
    }
    assert r.revert.params == {"replicas": 3} and "dryRun" not in seen[-1][2]

    r = await k.execute(act(ActionType.ROLLBACK))
    _, _, _, ctype, patch = seen[-1]
    assert ctype == "application/json-patch+json" and patch[0]["path"] == "/spec/template"
    tmpl = patch[0]["value"]
    assert tmpl["spec"]["containers"][0]["image"] == "app:v2"  # 현재(3) 직전 리비전
    assert "pod-template-hash" not in tmpl["metadata"]["labels"]


async def _ongoing(source, svc):
    ev = await RCAAnalyzer(source).collect(svc)
    return next(e for e in ev if e.service == svc).onset_min_ago is not None


async def test_simulated_executor_only_right_action_fixes():
    src = SimulatedOpsSource(seed=1)
    src.apply_scenario(
        FaultScenario(
            id="x",
            alert_service="order-service",
            faults=[
                Fault(
                    service="order-service",
                    metric="error_rate",
                    magnitude=10,
                    onset_min_ago=20,
                    fixed_by=["rollback"],
                )
            ],
        )
    )
    svc = RemediationService(SimulatedExecutor(src), Guardrails(GuardrailPolicy(cooldown_s=0)))
    assert await _ongoing(src, "order-service")
    r = await svc.execute(act(ActionType.RESTART), approved_by="sre")
    assert r.ok and "해소된 장애 0건" in r.detail and await _ongoing(src, "order-service")
    r = await svc.execute(act(ActionType.ROLLBACK), approved_by="sre")
    assert "해소된 장애 1건" in r.detail and not await _ongoing(src, "order-service")


async def test_service_requires_approver_for_write_actions():
    src = SimulatedOpsSource()
    svc = RemediationService(SimulatedExecutor(src))
    assert (await svc.execute(act(ActionType.RESTART), dry_run=True)).ok  # dry-run 은 허용
    denied = await svc.execute(act(ActionType.RESTART))
    assert not denied.ok and "승인자" in denied.detail
