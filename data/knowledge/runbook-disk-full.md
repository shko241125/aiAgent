# 디스크 가득 참(Disk Full) 대응 Runbook

## 증상
- 애플리케이션/DB 로그에 `No space left on device` 에러
- 쓰기 요청 실패, DB 가 read-only 로 전환, 컨테이너 재시작 반복(Evicted)
- node_filesystem_avail_bytes 급감, kubelet DiskPressure 이벤트

## 원인 후보
1. 로그 로테이션 누락으로 로그 파일 무한 증가
2. 임시 파일·코어 덤프·오래된 백업 누적
3. inode 고갈 (용량은 남았지만 작은 파일이 너무 많음) — `df -i` 로 확인
4. DB WAL/binlog 보관 기간 과다

## 조치 절차
1. `du -xh --max-depth=1 /` 로 큰 디렉터리 확인, 삭제 전 반드시 원인 파일 소유 프로세스 확인
2. 삭제된 파일을 프로세스가 계속 잡고 있으면(`lsof | grep deleted`) 프로세스 재시작 필요
3. 로그는 삭제 대신 truncate, 로그 로테이션 설정 복구
4. DB 볼륨이면 binlog/WAL 정리 정책 조정 후 볼륨 확장

## 재발 방지
- 디스크 사용률 80% 경고, 90% 긴급 알람
- 로그 보관 정책과 logrotate 설정 코드화
