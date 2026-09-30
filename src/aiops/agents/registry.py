from aiops.agents.base import BaseAgent


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, *agents: BaseAgent) -> None:
        for a in agents:
            self._agents[a.name] = a

    def get(self, name: str) -> BaseAgent:
        if name not in self._agents:
            raise KeyError(f"unknown agent: {name}")
        return self._agents[name]

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def describe(self) -> list[dict[str, str]]:
        return [{"name": a.name, "description": a.description} for a in self._agents.values()]
