# Kafka Consumer Lag 급증 대응 Runbook

## 증상
- consumer group lag 지속 증가, 처리 지연으로 후속 서비스(알림·정산) 지연
- 잦은 리밸런싱(rebalance) 로그: `Attempt to heartbeat failed since group is rebalancing`
- 컨슈머 처리 시간(p95) 증가

## 원인 후보
1. 컨슈머 처리 로직 지연(외부 API·DB 호출 느려짐) — 가장 흔함
2. max.poll.interval.ms 초과로 인한 리밸런싱 루프
3. 파티션 수 < 컨슈머 수 (늘려도 효과 없음)
4. 특정 키 쏠림(hot partition)

## 조치 절차
1. 파티션별 lag 확인 → 한 파티션만 높으면 키 쏠림
2. 컨슈머 처리 시간과 의존 서비스 지연 대조
3. 처리 병렬도 조정, 필요 시 파티션 증설(순서 보장 영향 검토)
4. 리밸런싱 루프면 poll 간격·배치 크기 조정
