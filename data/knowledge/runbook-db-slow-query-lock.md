# DB 슬로우 쿼리 · 락 경합 대응 Runbook

## 증상
- `Lock wait timeout exceeded`, `deadlock detected` 에러
- DB active session 급증, 커넥션 풀 대기 증가 → 애플리케이션 커넥션 풀 고갈로 번짐
- 특정 테이블 관련 API 만 느려짐

## 원인 후보
1. 인덱스 없는 조건으로 대량 스캔 (신규 쿼리·통계 정보 변경)
2. 장시간 트랜잭션이 락 보유 (배치 작업, 트랜잭션 미종료)
3. 동시 업데이트 순서 불일치로 인한 데드락

## 조치 절차
1. 현재 실행 중 쿼리와 대기 관계 조회 (pg_stat_activity / information_schema.innodb_trx)
2. 락을 쥔 장시간 트랜잭션 식별 후 종료 여부 결정 (영향 확인 필수)
3. 실행 계획(EXPLAIN) 확인, 인덱스 추가는 온라인 방식으로
4. 배치 작업 시간대 조정
