from aiops.agents.memory.base import ConversationMemory, InMemoryMemoryStore, MemoryItem
from aiops.llm.base import ChatMessage, Role, ToolCall
from aiops.prompts.registry import PromptRegistry, parse_template


def test_conversation_window_keeps_system_and_tool_pairs():
    mem = ConversationMemory(window=2)
    mem.add(ChatMessage.system("s"), ChatMessage.user("u1"))
    mem.add(ChatMessage.assistant(None, [ToolCall(id="1", name="f")]))
    mem.add(ChatMessage.tool("1", "f", "r"), ChatMessage.assistant("done"))
    msgs = mem.messages()
    assert msgs[0].role == Role.SYSTEM
    assert msgs[1].role != Role.TOOL  # 짝 잃은 tool 결과로 시작하지 않는다


async def test_long_term_memory_search():
    store = InMemoryMemoryStore()
    await store.add(MemoryItem(namespace="svc:a", content="커넥션 풀 고갈로 롤백"))
    await store.add(MemoryItem(namespace="svc:b", content="커넥션 풀 고갈"))
    hits = await store.search("svc:a", "커넥션 풀 문제")
    assert len(hits) == 1 and hits[0].namespace == "svc:a"


def test_all_templates_parse_and_render():
    reg = PromptRegistry()
    assert {
        "detection",
        "rca",
        "remediation",
        "incident",
        "report",
        "knowledge",
        "supervisor",
        "rag_answer",
    } <= set(reg.names())
    r = reg.render("rag_answer", question="Q?", context="CTX")
    assert "CTX" in r.system and r.user == "Q?"


def test_template_keeps_json_braces():
    t = parse_template("x", '===system===\n출력: {"a": $v}\n===user===\n$input')
    assert t.system == '출력: {"a": $v}'
