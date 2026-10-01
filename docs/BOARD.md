# 작업 보드 (BOARD)

> ⚠️ 자동 생성 파일 — 직접 수정하지 마세요. 원본: `tracking/cards/*.json`
> 갱신: `make docs` (편집 시 Claude Code 훅·pre-commit 이 자동 실행)

**📥 backlog 0 · 🟦 ready 4 · 🔄 in_progress 0 · 👀 review 0 · ⛔ blocked 1 · ✅ done 18**

## ⛔ blocked (1)

| 카드 | 제목 | 우선 | 담당 | 상위 | 인계 메모 / 막힌 사유 |
|---|---|---|---|---|---|
| M1-10 | 실 LLM 품질 측정 (Claude/OpenAI/구축형 Gemma·Qwen) | p2 | claude-code | R-4.1 | 실 LLM API 키(AIOPS_ANTHROPIC/OPENAI/GEMINI_API_KEY) 또는 구축형 모델 서버(vLLM/Ollama: Gemma·Qwen) 필요 |

## 🟦 ready (4)

| 카드 | 제목 | 우선 | 담당 | 상위 | 인계 메모 / 막힌 사유 |
|---|---|---|---|---|---|
| M2-05 | 상황 인식 가중치 학습(로지스틱 회귀) + 토폴로지 영향 전파 | p1 | - | R-3.2 |  |
| M2-06 | 변화점 탐지 + 메트릭 상관 + fact sheet 표준 스키마 | p1 | - | R-3.5 |  |
| M2-07 | 장애 판정(is_incident) 결정적 평가 | p1 | - | R-2.1 |  |
| M2-08 | 실 Prometheus·Loki·Alertmanager 연동 검증 | p2 | - | R-2.1 |  |

## ✅ done (18)

| 카드 | 제목 | 우선 | 담당 | 상위 | 인계 메모 / 막힌 사유 |
|---|---|---|---|---|---|
| KB-01 | 칸반 도메인 모델·정책 (컬럼·전이·WIP·lease·DoD) | p1 | claude-code | R-1.2 | models.py/policy.py 완료. 전이표 ALLOWED_TRANSITIONS, WIP(컬럼/담당자), lease, DoD. 테스트: tests/unit/kanban/test_board.py |
| KB-02 | 칸반 저장소 3종 (memory/file/SQL) + 낙관적 동시성 | p1 | claude-code | R-1.2 | stores.py: InMemory/File/Sql. 개발 보드는 FileBoardStore(tracking/cards). 파일 저장소는 단일 프로세스 가정 — 동시 다중 쓰기는 git merge 로 해소 |
| KB-03 | 브리핑 · 에이전트용 칸반 도구 · ToolRuntime 주입 | p1 | claude-code | R-1.6 | briefing.py(렌더), tools.py(board_* 7종), ToolRuntime 은 agents/tools/base.py. LLMAgent 가 task.card_id 있으면 브리핑 주입 + 보드 도구 개방 |
| KB-04 | 오케스트레이터 통합 (카드 수명주기·Pull 루프·인시던트 보드) + API | p1 | claude-code | R-1.4 | Orchestrator.run_agent 가 claim→실행→outputs→DONE/REVIEW/BLOCKED. run_kanban(Pull)+plan_cards(Planner). build_incident_res… |
| KB-05 | 개발 보드 이관 · 문서 자동 연동 · CLAUDE.md · 훅 · CI | p1 | claude-code | R-1.2 | CLAUDE.md(최상위 규칙 R1~R12), .claude/settings.json(SessionStart/PostToolUse/Stop), .githooks(pre-commit 문서 재생성·스테이징, commi… |
| M1-01 | 구조화 출력 스키마 검증 + 1회 재시도 | p1 | claude-code | R-4.1 | agents/schemas.py(Detection/RCA/Remediation Output), LLMAgent.output_model/output_retries, AgentResult.schema_errors. R… |
| M1-02 | LLM 사용량(토큰·지연·비용) 집계 + API | p1 | claude-code | R-4.1 | llm/usage.py(normalize_usage, UsageTracker, current_agent), LLMRouter 가 호출마다 기록(성공/실패·지연), GET /api/v1/llm/usage?group_… |
| M1-03 | 에이전트별 모델 라우팅 + Gemini tool calling + 프로바이더 계약 테스트 | p1 | claude-code | R-4.1 | gemini.py function calling, LLMRouter.bind()/BoundLLM + AIOPS_AGENT_LLM_PROVIDERS, 프로바이더 http_client 주입. 계약 테스트 tests/u… |
| M1-04 | Reranker (HTTP cross-encoder · LLM listwise) | p1 | claude-code | R-4.2 | rag/rerankers.py: HTTPReranker(tei:/rerank, jina:/v1/rerank), LLMReranker(listwise JSON 점수). AIOPS_RERANKER=none\|http\… |
| M1-05 | 증분 인제스트 (content hash · 문서 교체/삭제) | p1 | claude-code | R-4.2 | RAGService.ingest → IngestReport(added/updated/skipped). sha256(text+metadata) 비교, 변경 시 delete_doc(BM25.remove_doc + Ve… |
| M1-06 | 검색 평가 하네스 + 평가셋 + 운영 지식 확충 | p1 | claude-code | R-4.2 | rag/evaluation.py(Recall@k·MRR·nDCG·유형별), data/eval/retrieval.jsonl(30), scripts/eval_retrieval.py(bm25/vector/hybrid/+… |
| M1-07 | 프롬프트 eval 러너 + 시나리오 + RCA few-shot(v2) | p1 | claude-code | R-4.3 | evals/prompts.py(PromptScenario·Check 7종·RecordingLLM·run_suite, 결과에 prompt 이름/버전), data/eval/prompt_scenarios.json(6: … |
| M1-08 | RCA 원인 후보 랭킹 엔진 + 장애 시나리오 평가 | p1 | claude-code | R-2.2 | analytics/rca.py(RCAAnalyzer: downstream depth3 근거 수집, rank_candidates: change/error_signature/dependency/resource, 시그니… |
| M1-09 | 인용 검증 + 해결 인시던트 지식 자동 축적 | p1 | claude-code | R-2.6 | rag/citations.py(문서id·번호 인용, invalid, citation_rate), RAG answer 에 citation_report, KnowledgeAgent 가 검색 결과 기준으로 검증(data… |
| M2-01 | Prometheus·Loki 어댑터 + 소스 조합(Composite) + 토폴로지 파일 | p1 | claude-code | R-2.1 | integrations/prometheus.py(DEFAULT_QUERIES: k8s+Micrometer 기준, 파일로 덮어쓰기), loki.py(LogQL, 나노초, X-Scope-OrgID), composite… |
| M2-02 | 이벤트 수집: Alertmanager·CI/CD 웹훅 → DB 이벤트 저장소 | p1 | claude-code | R-2.1 | integrations/events.py(Alertmanager v4·ChangePayload 파싱, SqlEventStore=EventSource, id 기반 멱등), db OpsEventRow, API /api… |
| M2-03 | 이상 탐지 평가 하네스 + 계절성 탐지기 + 스트리밍 인터페이스 | p1 | claude-code | R-3.1 | anomaly/synthetic.py(패턴 6종 라벨 생성), evaluation.py(이벤트 단위 P/R/F1·지연·헛알람 묶음), seasonal.py(ACF 주기 추정 + 과거 주기 중앙값 잔차 robust … |
| M2-04 | 로그 템플릿 추출(Drain) + 알람 폭주 압축률 | p1 | claude-code | R-3.3 | analytics/logs.py(DrainParser: 고정 깊이 트리·유사도 0.5·변수 마스킹, summarize_logs, grouping_accuracy), logs_synthetic.py, events.a… |
