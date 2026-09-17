#!/bin/sh
# 만료 정리를 주기적으로 실행한다.
#
# 호스트 배포에서는 systemd 타이머가 하는 일이다. 컨테이너에는 cron 을 넣지
# 않는다 — 프로세스가 하나 더 늘고, cron 의 로그가 컨테이너 표준 출력으로
# 나오지 않아 docker logs 로 볼 수 없게 된다.
#
# **이것이 파일이 실제로 사라지는 주 경로다.** 이 컨테이너가 죽어 있으면
# 만료된 링크는 즉시 410 이 되지만(앱이 expires_at 을 비교한다) 디스크의
# 파일은 계속 쌓인다.
set -eu

INTERVAL="${SWEEP_INTERVAL_SECONDS:-300}"

# SIGTERM 을 받으면 대기 중이라도 즉시 끝낸다. 이게 없으면 docker stop 이
# 기본 10초를 기다렸다가 SIGKILL 로 죽여서, 종료가 매번 느려진다.
running=1
trap 'running=0' TERM INT

echo "sweeper: ${INTERVAL}초 주기로 실행합니다"

while [ "$running" -eq 1 ]; do
    python -m app.sweeper || echo "sweeper: 이번 회차 실패 — 다음 주기에 다시 시도합니다" >&2
    # sleep 을 백그라운드로 두고 wait 해야 트랩이 즉시 걸린다.
    sleep "$INTERVAL" &
    wait $! || true
done

echo "sweeper: 종료"
