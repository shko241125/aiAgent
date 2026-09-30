# PLAN-0002: M1 — 실 LLM + 실 지식으로 RCA 품질 확보

- 범위: 로드맵 4.1 · 4.2 · 4.3 · 2.2 · 2.6
- 진행: 아래 카드 표는 보드에서 자동 생성된다 (전체 보드: [BOARD.md](../BOARD.md))

## 1. 목표

M0 는 "끝까지 돈다"를 증명했다. M1 은 **"맞는 답을 낸다"를 측정 가능하게** 만드는 단계다.
품질을 올리기 전에 먼저 **재는 도구**(평가 하네스)를 만들고, 그 위에서 개선을 쌓는다.

## 2. 설계 판단

| 판단 | 이유 |
|---|---|
| **결정적 RCA 후보 랭킹을 LLM 앞에 둔다** (M1-08) | LLM 에게 원인을 "창작"시키지 않고, 코드가 뽑은 후보(근거 포함)를 검증·선택하게 한다. LLM 없이도 Top-k 적중률을 잴 수 있어 회귀 기준선이 된다. |
| **출력 스키마 검증 + 1회 재시도** (M1-01) | 에이전트 간 인계는 구조화 데이터로 이뤄진다. 형식 오류가 다음 단계로 전파되지 않게 경계에서 막는다. |
| **평가 = 코드** (M1-06, M1-07) | 평가셋과 지표를 저장소에 두고 CI 에서 scripted LLM 으로 회귀, 실 모델은 스크립트로 측정. 프롬프트·검색 변경이 품질에 주는 영향을 숫자로 본다. |
| **Reranker 는 외부 서비스 호출** (M1-04) | cross-encoder 는 GPU 서빙(TEI 등)이 표준. 코드는 HTTP 계약만 알고, 없으면 LLM 재정렬 또는 생략. |
| **실 모델 측정은 별도 카드** (M1-10) | 이 환경에는 API 키·GPU 가 없다. 코드 경로는 MockTransport 계약 테스트로 검증하고, 실측은 키가 주어지면 바로 실행할 수 있게 스크립트로 준비한다. |

## 3. 카드

<!-- AUTO:CARDS label=plan:0002 -->
| 카드 | 제목 | 상태 | 담당 | 인계 메모 / 막힌 사유 |
|---|---|---|---|---|
| M1-01 | 구조화 출력 스키마 검증 + 1회 재시도 | ✅ done | claude-code | agents/schemas.py(Detection/RCA/Remediation Output), LLMAgent.output_model/output_retries, AgentResult.schema_errors. R… |
| M1-02 | LLM 사용량(토큰·지연·비용) 집계 + API | ✅ done | claude-code | llm/usage.py(normalize_usage, UsageTracker, current_agent), LLMRouter 가 호출마다 기록(성공/실패·지연), GET /api/v1/llm/usage?group_… |
| M1-03 | 에이전트별 모델 라우팅 + Gemini tool calling + 프로바이더 계약 테스트 | ✅ done | claude-code | gemini.py function calling, LLMRouter.bind()/BoundLLM + AIOPS_AGENT_LLM_PROVIDERS, 프로바이더 http_client 주입. 계약 테스트 tests/u… |
| M1-04 | Reranker (HTTP cross-encoder · LLM listwise) | ✅ done | claude-code | rag/rerankers.py: HTTPReranker(tei:/rerank, jina:/v1/rerank), LLMReranker(listwise JSON 점수). AIOPS_RERANKER=none\|http\… |
| M1-05 | 증분 인제스트 (content hash · 문서 교체/삭제) | ✅ done | claude-code | RAGService.ingest → IngestReport(added/updated/skipped). sha256(text+metadata) 비교, 변경 시 delete_doc(BM25.remove_doc + Ve… |
| M1-06 | 검색 평가 하네스 + 평가셋 + 운영 지식 확충 | ✅ done | claude-code | rag/evaluation.py(Recall@k·MRR·nDCG·유형별), data/eval/retrieval.jsonl(30), scripts/eval_retrieval.py(bm25/vector/hybrid/+… |
| M1-07 | 프롬프트 eval 러너 + 시나리오 + RCA few-shot(v2) | 🟦 ready | - |  |
| M1-08 | RCA 원인 후보 랭킹 엔진 + 장애 시나리오 평가 | 🟦 ready | - |  |
| M1-09 | 인용 검증 + 해결 인시던트 지식 자동 축적 | 🟦 ready | - |  |
| M1-10 | 실 LLM 품질 측정 (Claude/OpenAI/구축형 Gemma·Qwen) | 🟦 ready | - |  |

진행: 6/10
<!-- /AUTO -->

## 4. 완료 판정

- 코드: 모든 카드의 DoD 체크 + `make check` 통과.
- 품질 기준선(결정적 경로)은 각 카드 outputs 에 기록한다 (`show M1-06`, `show M1-08`).
- 실 모델 품질표(M1-10)가 나오면 M1 을 종료하고 결과를 이 문서 §5 에 요약한다.
