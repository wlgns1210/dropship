"""배포에 필요한 비밀값을 만든다.

    python scripts/gen_secrets.py

출력된 값을 GitHub 저장소 시크릿(Settings → Secrets and variables → Actions)에
등록한다. 저장소에 커밋하지 않는다.

IP_HASH_SALT 는 한 번 정하면 바꾸지 않는 것이 좋다. 바꾸면 진행 중인 일일 쿼터
집계가 초기화되어 그날 한도를 다 쓴 사람도 다시 올릴 수 있게 된다.
"""

import secrets

FIELDS = {
    "ORIGIN_SECRET": "CloudFront 가 오리진 요청에 붙이는 공유 비밀",
    "IP_HASH_SALT": "IP 해시용 솔트 (한 번 정하면 바꾸지 말 것)",
}

if __name__ == "__main__":
    print("GitHub 저장소 시크릿에 등록할 값입니다. 커밋하지 마세요.\n")
    for name, description in FIELDS.items():
        print(f"# {description}")
        print(f"{name}={secrets.token_urlsafe(48)}\n")
