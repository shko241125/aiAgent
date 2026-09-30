# 아키텍처

## 1. 전체 흐름

```
 [모니터링/로그/변경이력]                                     [운영자 / 외부 시스템]
  Prometheus·Loki·K8s·CI/CD                                     Slack·ITSM·대시보드
          │ integrations/*                                             ▲
          ▼                                                            │ api/*
 ┌─────────────────────┐   fact sheet   ┌──────────────────────────────┴──────────┐
 │ analytics (3.x)      │──────────────▶│ Orchestrator (1.2~1.4)                   │
 │  이상탐지 → 상황인식  │               │  Pipeline │ Workflow(DAG) │ Supervisor   │
 │  이벤트상관 → 예측    │               └───┬──────────┬──────────┬──────────────┘
 └─────────────────────┘                   │          │          │   Blackboard(공유 상태)
                                           ▼          ▼          ▼
                         Detection ─▶ RCA ─▶ Remediation ─▶ Incident ─▶ Report   (2.x)
                             │         │          │
                             ▼         ▼          ▼
                     ToolRegistry (1.5) — 위험도 기반 승인 정책 (HITL)
                             │
             ┌───────────────┼──────────────────┐
             ▼               ▼                  ▼
        운영 데이터 조회   RAG 지식검색 (4.2)    자동 조치 (재시작/스케일/롤백)
                           BM25 + Vector → RRF
                             │
                     LLMRouter (4.1): OpenAI · Claude · Gemini · 구축형(vLLM/Ollama) + fallback
```

## 2. 핵심 설계 원칙

1. **수치는 코드가, 해석은 LLM 이.** 이상 탐지·상황 인식·예측은 결정적 코드(analytics)가 수행하고,
   LLM 에는 압축된 fact sheet 만 전달한다 → 토큰 절감, 환각 감소, 재현성 확보.
2. **벤더 독립.** 에이전트는 `LLMProvider`/`VectorStore`/`Embedder`/`MetricsSource` 인터페이스에만 의존한다.
   구현체 교체는 `core/container.py` 한 곳에서.
3. **안전한 자동화.** 모든 도구는 위험도(`read`/`write`/`destructive`)를 가진다.
   `write` 는 정책에 따라, `destructive` 는 항상 사람 승인이 필요하다. 에이전트는 승인을 우회할 수 없다.
4. **관측 가능한 에이전트.** 모든 LLM·도구 호출은 `AgentContext.trace` 에 기록되고 DB(`agent_runs`)에 저장된다.
5. **프레임워크는 교체 가능한 어댑터.** 자체 경량 오케스트레이터가 기본이고,
   LangGraph 는 어댑터(`langgraph_adapter.py`)로 붙인다. → [ADR-0001](adr/0001-agent-framework.md)

## 3. 에이전트 실행 모델 (1.1)

`LLMAgent.run()` 은 Template Method 패턴이다.

```
build_input(task, ctx)          # 서브클래스: blackboard / 분석 결과를 프롬프트 변수로 주입
  → render prompt (prompts/templates/<name>.md)
  → loop (≤ max_steps):
       llm.chat(messages, tools) ─┬─ tool_calls 있음 → ToolRegistry.invoke → tool 메시지 추가 → 반복
                                  └─ 없음 → 최종 답변, JSON 추출(result.data)
  → post_process(result, ctx)   # 기본: blackboard 에 '<agent>.output', '<agent>.data' 기록
```

## 4. 협업 패턴 (1.2)

| 패턴 | 언제 | 구현 |
|---|---|---|
| Pipeline | 단계가 고정된 절차 | `Orchestrator.run_sequential` |
| Workflow(DAG) | 병렬·조건 분기·재시도가 필요한 절차 | `WorkflowEngine` + `Orchestrator.agent_step` |
| Supervisor | 목표만 주고 경로는 LLM 이 결정 | `Orchestrator.run_supervised` + `prompts/templates/supervisor.md` |

에이전트 간 데이터 교환은 **Blackboard**(키 규약 `<agent>.<field>`)로만 한다. 에이전트끼리 직접 호출하지 않는다.

## 5. 메모리 계층 (1.6)

| 계층 | 수명 | 구현 |
|---|---|---|
| 대화(작업) 메모리 | 에이전트 1회 실행 | `ConversationMemory` (window, tool 쌍 보존) |
| 공유 상태 | 오케스트레이션 1회 | `Blackboard` |
| 장기 메모리 | 영구 | `MemoryStore` (현재 in-memory, → Vector DB) — 과거 인시던트를 RCA 에 재사용 |

## 6. RAG (4.2)

```
Markdown/문서 → chunk_document (헤딩 경로 보존) → ┬ BM25Index (한글 bigram 토크나이저)
                                                 └ Embedder → VectorStore (memory | Qdrant)
질의 → BM25 top-N + Vector top-N → RRF 융합 → (Reranker) → top-k → LLM(근거 인용 강제)
```

## 7. 데이터 저장 (4.4)

- 운영 DB: SQLAlchemy async (로컬 SQLite / 운영 PostgreSQL). `incidents`, `agent_runs`.
- Vector DB: Qdrant. 시계열 원본은 기존 모니터링 시스템에 두고 조회만 한다.
