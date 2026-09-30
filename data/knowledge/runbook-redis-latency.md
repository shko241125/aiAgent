# Redis 지연 대응 Runbook

## 증상
- 캐시 조회 지연으로 API latency 상승, `redis timeout` / `Command timed out` 에러
- Redis CPU 100%(단일 스레드), slowlog 증가
- evicted_keys 급증, 캐시 적중률 하락 → DB 부하 전이

## 원인 후보
1. `KEYS *`, 대형 `HGETALL`/`SMEMBERS` 등 O(N) 명령
2. big key (수 MB 값) 조회·삭제
3. maxmemory 도달로 eviction 폭증
4. 캐시 만료 시각 집중(cache stampede)

## 조치 절차
1. `SLOWLOG GET 20`, `INFO stats` 로 원인 명령 특정
2. O(N) 명령 사용 코드 차단/수정(SCAN 으로 대체)
3. big key 분할, UNLINK 로 비동기 삭제
4. TTL 에 지터 추가로 만료 분산
