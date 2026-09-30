"""LangGraph 어댑터 (1.x, 선택) — `pip install .[agents]` 필요.

자체 Orchestrator 가 기본이며, LangGraph 의 체크포인트·HITL interrupt·시각화 기능이 필요할 때
동일한 에이전트들을 LangGraph StateGraph 노드로 감싸 실행할 수 있게 한다.
(→ 프레임워크 선택을 에이전트 구현과 분리: docs/adr/0001-agent-framework.md)

TODO(1.4): 조건부 엣지(add_conditional_edges)로 Supervisor 라우팅, checkpointer 로 재개 지원.
"""

from typing import Any, TypedDict

from aiops.agents.base import AgentTask
from aiops.agents.context import AgentContext
from aiops.agents.registry import AgentRegistry


class GraphState(TypedDict, total=False):
    instruction: str
    inputs: dict[str, Any]
    outputs: dict[str, str]


def build_linear_graph(agents: AgentRegistry, names: list[str], ctx: AgentContext):
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(GraphState)

    def make_node(name: str):
        async def node(state: GraphState) -> GraphState:
            task = AgentTask(
                instruction=state.get("instruction", ""), inputs=state.get("inputs", {})
            )
            res = await agents.get(name).run(task, ctx)
            return {"outputs": {**state.get("outputs", {}), name: res.output}}

        return node

    for n in names:
        graph.add_node(n, make_node(n))
    graph.add_edge(START, names[0])
    for a, b in zip(names, names[1:], strict=False):
        graph.add_edge(a, b)
    graph.add_edge(names[-1], END)
    return graph.compile()
