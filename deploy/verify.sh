#!/usr/bin/env bash
# 배포 후 스모크 점검.
#
#   bash deploy/verify.sh [기준주소]
#
# 기준주소를 생략하면 http://localhost:8090 을 본다 — SKIFF_PORT 의 기본값이다.
# 컨테이너는 호스트의 8090 에 붙고 컨테이너 안에서만 80 이다. docker ps 의
# "0.0.0.0:8090->80/tcp" 에서 바깥에서 닿는 것은 왼쪽이다.
#
# **상태 코드만 보지 않는다.** 이 서비스는 매칭되지 않는 경로를 전부
# 404.html 로 돌려주면서 200 을 낸다(공유 링크를 살리기 위한 설계다).
# 그래서 "200 이 왔다" 는 페이지가 제대로 떴다는 증거가 되지 못한다 —
# 실제로 /admin 이 404 화면을 200 으로 돌려주는데도 점검을 통과한 적이 있다.
# 각 페이지가 자기 내용을 담고 있는지를 본문으로 확인한다.
set -uo pipefail

BASE="${1:-http://localhost:8090}"
PASS=0
FAIL=0

check_body() {
    local label="$1" path="$2" needle="$3"
    local body
    body=$(curl -sk --max-time 20 "$BASE$path" 2>/dev/null)
    if printf '%s' "$body" | grep -q -- "$needle"; then
        printf '  OK   %-28s (%s 포함)\n' "$label" "$needle"
        PASS=$((PASS + 1))
    else
        printf '  FAIL %-28s (%s 없음)\n' "$label" "$needle"
        FAIL=$((FAIL + 1))
    fi
}

check_status() {
    local label="$1" path="$2" expected="$3" extra="${4:-}"
    local code
    # shellcheck disable=SC2086
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 $extra "$BASE$path" 2>/dev/null)
    if [ "$code" = "$expected" ]; then
        printf '  OK   %-28s (%s)\n' "$label" "$code"
        PASS=$((PASS + 1))
    else
        printf '  FAIL %-28s (%s, 기대 %s)\n' "$label" "$code" "$expected"
        FAIL=$((FAIL + 1))
    fi
}

echo "점검 대상: $BASE"
echo ""

# 먼저 닿는지부터 본다.
#
# 이게 없으면 서버에 연결조차 못 한 경우에도 FAIL 이 11줄 쏟아진다. 항목마다
# 틀린 것처럼 보여서, 정작 원인(포트를 잘못 봤다)이 그 속에 묻힌다.
if ! curl -sk -o /dev/null --max-time 10 "$BASE/api/health" 2>/dev/null; then
    printf '  연결할 수 없다: %s

' "$BASE"
    echo "  컨테이너가 떠 있는데도 이렇다면 포트를 확인한다."
    echo "  docker ps 의 \"0.0.0.0:8090->80/tcp\" 에서 **왼쪽**이 바깥에서 닿는 포트다."
    echo "  오른쪽 80 은 컨테이너 안에서만 쓰인다."
    echo ""
    echo "    docker ps --format '{{.Names}}	{{.Ports}}'"
    echo "    bash verify.sh http://127.0.0.1:<그 포트>"
    exit 1
fi

echo "[페이지 — 본문으로 확인]"
check_body "업로드 화면 /"        "/"              "끌어다 놓으세요"

# 관리자 화면은 클라이언트에서 그려지므로 정적 HTML 에 최종 문구("관리자 토큰")가
# 없다. 대신 **그 페이지 전용 청크를 참조하는지**를 본다. 404 폴백이 떴다면
# 이 경로가 HTML 에 없으므로, 이것이 정확한 판별 기준이다.
check_body "관리자 화면 /admin"   "/admin"         "chunks/app/admin/page-"

# 공유 링크 경로는 앱 셸(404.html)이 떠야 한다. 이건 의도된 동작이다.
check_body "공유 링크 폴백"       "/oslo/123456"   "chunks/app/not-found-"

# 관리자 청크가 공유 링크 폴백에는 없어야 한다. 있으면 라우팅이 뒤섞인 것이다.
if curl -sk --max-time 20 "$BASE/oslo/123456" | grep -q "chunks/app/admin/page-"; then
    printf '  FAIL %-28s (관리자 청크가 섞임)\n' "폴백/관리자 분리"
    FAIL=$((FAIL + 1))
else
    printf '  OK   %-28s\n' "폴백/관리자 분리"
    PASS=$((PASS + 1))
fi

echo ""
echo "[API]"
check_body "헬스체크"             "/api/health"    '"status":"ok"'
check_body "설정"                 "/api/config"    "max_total_bytes"
check_status "없는 코드는 410"    "/api/transfers/oslo/999999" "410"
check_status "관리자 인증 필요"   "/api/admin/stats"           "401"

echo ""
echo "[보안]"
check_status "내부 파일 경로 차단" "/protected/anything"        "200"
check_status "조작 다운로드 토큰"  "/api/d/fake.token"          "403"
check_status "조작 업로드 토큰"    "/api/upload/fake.token"     "403" "-X PUT"

echo ""
echo "════════════════════════════════"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
