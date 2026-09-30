from aiops.agents.tools.base import ApprovalPolicy, ToolRegistry, ToolRisk, tool
from aiops.llm.base import ToolCall


def test_schema_generated_from_signature(echo_tools):
    spec = echo_tools.specs()[0]
    assert spec.name == "add"
    assert spec.description == "두 수를 더한다."
    assert spec.parameters["required"] == ["a"]
    assert spec.parameters["properties"]["a"]["type"] == "integer"


async def test_invoke_validates_arguments(echo_tools):
    ok = await echo_tools.invoke(ToolCall(id="1", name="add", arguments={"a": 2, "b": 3}))
    assert ok.ok and ok.output == 5
    bad = await echo_tools.invoke(ToolCall(id="2", name="add", arguments={"a": "x"}))
    assert not bad.ok and bad.error


async def test_write_tool_requires_approval():
    @tool(risk=ToolRisk.WRITE)
    async def restart(service: str) -> str:
        """restart"""
        return f"restarted {service}"

    reg = ToolRegistry(ApprovalPolicy(auto_approve=False))
    reg.register(restart)
    call = ToolCall(id="1", name="restart", arguments={"service": "a"})
    pending = await reg.invoke(call)
    assert pending.pending_approval and not pending.ok
    approved = await reg.invoke(call, approved={"restart"})
    assert approved.ok and approved.output == "restarted a"


async def test_destructive_always_requires_approval():
    @tool(risk=ToolRisk.DESTRUCTIVE)
    def drop() -> None:
        """drop"""

    reg = ToolRegistry(ApprovalPolicy(auto_approve=True))
    reg.register(drop)
    res = await reg.invoke(ToolCall(id="1", name="drop"))
    assert res.pending_approval
