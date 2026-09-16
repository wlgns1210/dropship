"""실제 HTTP 로 presigned URL 왕복을 확인하는 E2E 점검.

단위 테스트(moto)가 못 보는 것을 본다. moto 가 만든 서명 URL 은 진짜 AWS 주소를
가리켜서 테스트에서 PUT 할 수 없기 때문에, tests/ 는 boto3 로 파트를 올린다.
여기서는 **브라우저가 하는 그대로** presigned URL 에 HTTP PUT 을 날린다.

그래서 여기서만 잡히는 것들:
  - 서명이 실제로 유효한가 (헤더 하나만 어긋나도 SignatureDoesNotMatch)
  - ETag 를 응답에서 읽을 수 있는가
  - 8MiB 를 넘는 진짜 멀티파트가 닫히는가
  - 한글 파일명이 Content-Disposition 을 거쳐 원본으로 복원되는가

    make dev-api                                    # 다른 터미널
    python scripts/e2e_check.py                     # 로컬(LocalStack)

    DROPSHIP_API=https://xxxx.cloudfront.net \\
        python scripts/e2e_check.py                 # 배포된 환경

배포 후에도 같은 스크립트를 돌릴 수 있어야 한다. 로컬에서만 확인하고 배포는
눈으로 때우면, 로컬과 운영의 차이(서명 방식, CORS, 헤더 전달)에서 나는 문제를
사용자가 먼저 발견하게 된다.
"""

import os
import sys
import time
import unicodedata
from urllib.parse import unquote

import httpx

API = os.environ.get("DROPSHIP_API", "http://localhost:8000").rstrip("/")
PART_SIZE = 8 * 1024 * 1024

_passed = 0
_failed: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def absolute(url: str) -> str:
    """상대 URL 을 API 기준으로 절대화한다.

    AWS 모드의 part_url 은 S3 절대 주소지만, 단일 노드 모드에서는 우리 서버의
    상대 경로(``/api/upload/...``)다. 같은 스크립트로 두 모드를 모두 점검하려면
    여기서 흡수해야 한다.
    """
    return url if url.startswith("http") else f"{API}{url}"


def upload_via_presigned(client: httpx.Client, upload: dict, blob: bytes) -> dict:
    """브라우저가 하는 그대로 파트별 PUT. Content-Type 헤더는 붙이지 않는다.

    서명에 포함되지 않은 헤더를 얹으면 S3 가 SignatureDoesNotMatch 로 거부한다.
    프론트의 uploader.ts 도 같은 이유로 헤더를 붙이지 않는다.
    """
    parts = []
    for number, url in enumerate(upload["part_urls"], start=1):
        chunk = blob[(number - 1) * PART_SIZE : number * PART_SIZE]
        response = client.put(absolute(url), content=chunk, timeout=120)
        if response.status_code != 200:
            raise RuntimeError(
                f"파트 {number} PUT 실패: {response.status_code} {response.text[:300]}"
            )
        etag = response.headers.get("ETag")
        if not etag:
            raise RuntimeError(f"파트 {number} 응답에 ETag 가 없다")
        parts.append({"part_number": number, "etag": etag})
    return {"file_index": upload["file_index"], "parts": parts}


def main() -> int:
    client = httpx.Client(follow_redirects=True)

    print("\n[1] 헬스체크")
    health = client.get(f"{API}/api/health")
    check("API 응답", health.status_code == 200, health.text[:200])

    config = client.get(f"{API}/api/config").json()
    check("상한이 1GB", config["max_total_bytes"] == 1024**3, str(config))

    # ── 한글 파일명 + 진짜 멀티파트(12MB → 2파트) ──
    print("\n[2] 업로드 세션 생성")
    korean_name = "보고서 최종본 🏖.pdf"
    blob = bytes(range(256)) * (12 * 1024 * 1024 // 256)  # 12MiB
    created = client.post(
        f"{API}/api/transfers",
        json={
            "files": [{"name": korean_name, "size": len(blob), "mime": "application/pdf"}],
            "expires_in": 3600,
        },
    )
    check("201 생성", created.status_code == 201, created.text[:300])
    if created.status_code != 201:
        return 1
    session = created.json()

    code = session["code"]
    word, number = code.split("/")
    check("코드 형식 word/6자리", word.isalpha() and len(number) == 6, code)
    check("멀티파트가 2파트로 쪼개짐", len(session["uploads"][0]["part_urls"]) == 2,
          str(len(session["uploads"][0]["part_urls"])))

    print(f"\n[3] presigned URL 로 실제 업로드 ({len(blob) / 1024 / 1024:.0f}MiB)")
    started = time.time()
    completed = upload_via_presigned(client, session["uploads"][0], blob)
    print(f"  업로드 {time.time() - started:.1f}초")
    check("모든 파트에서 ETag 수신", len(completed["parts"]) == 2)

    print("\n[4] 확정 전 링크는 죽어 있어야 한다")
    check("pending 은 410", client.get(f"{API}/api/transfers/{code}").status_code == 410)

    print("\n[5] 업로드 확정")
    done = client.post(
        f"{API}/api/transfers/{code}/complete",
        json={"owner_token": session["owner_token"], "files": [completed]},
    )
    check("200 확정", done.status_code == 200, done.text[:300])
    if done.status_code != 200:
        return 1
    check("실제 크기로 확정됨", done.json()["total_size"] == len(blob), str(done.json()))

    print("\n[6] 수신자 조회")
    info = client.get(f"{API}/api/transfers/{code}")
    check("200 조회", info.status_code == 200, info.text[:200])
    body = info.json()
    # macOS 가 보내는 NFD 와 구분하기 위해 NFC 로 비교한다.
    check(
        "한글·이모지 파일명 보존",
        body["files"][0]["name"] == unicodedata.normalize("NFC", korean_name),
        repr(body["files"][0]["name"]),
    )
    check("위험 확장자 아님", body["files"][0]["risky"] is False)

    print("\n[7] 다운로드 — 서명 URL 로 실제 내려받기")
    signed = client.get(f"{API}/api/transfers/{code}/download/0")
    check("200 서명 URL 발급", signed.status_code == 200, signed.text[:200])
    url = absolute(signed.json()["url"])

    fetched = client.get(url, timeout=120)
    check("S3 에서 200", fetched.status_code == 200, fetched.text[:300])
    check("바이트가 원본과 동일", fetched.content == blob,
          f"{len(fetched.content)} != {len(blob)}")

    disposition = fetched.headers.get("content-disposition", "")
    decoded = unquote(disposition.split("''")[-1]) if "''" in disposition else ""
    check(
        "Content-Disposition 이 한글 원본 파일명을 복원",
        decoded == unicodedata.normalize("NFC", korean_name),
        f"{disposition!r} → {decoded!r}",
    )

    print("\n[8] 열거 방어 — 없는 코드와 만료된 코드가 구분되지 않아야 한다")
    missing = client.get(f"{API}/api/transfers/{word}/999999")
    check("없는 코드는 410", missing.status_code == 410, str(missing.status_code))
    check("목록에 없는 단어도 410",
          client.get(f"{API}/api/transfers/zzzzz/123456").status_code == 410)

    print("\n[9] 소유자만 삭제할 수 있다")
    stranger = client.delete(f"{API}/api/transfers/{code}", params={"owner_token": "nope"})
    check("남의 토큰은 410", stranger.status_code == 410, str(stranger.status_code))
    check("삭제되지 않고 살아 있음", client.get(f"{API}/api/transfers/{code}").status_code == 200)

    owner = client.delete(
        f"{API}/api/transfers/{code}", params={"owner_token": session["owner_token"]}
    )
    check("소유자는 삭제 성공", owner.status_code == 200, owner.text[:200])
    check("삭제 후 410", client.get(f"{API}/api/transfers/{code}").status_code == 410)

    gone = client.get(url, timeout=60)
    check("S3 객체도 실제로 사라짐", gone.status_code in (403, 404), str(gone.status_code))

    print("\n" + "=" * 52)
    if _failed:
        print(f"통과 {_passed} · 실패 {len(_failed)}")
        for name in _failed:
            print(f"  - {name}")
        return 1
    print(f"전부 통과 ({_passed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
