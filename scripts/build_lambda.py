"""Lambda 배포 패키지를 만든다 → ``build/lambda/``

    python scripts/build_lambda.py

CDK 의 ``PythonFunction``(alpha) 대신 이 스크립트를 쓰는 이유:
  - alpha 모듈은 API 가 바뀔 수 있고 Docker 를 요구한다
  - 여기서는 pip 한 번이면 끝나고 CI 에서 Docker 없이 돈다

``--platform`` 을 명시해 **빌드 머신이 무엇이든** Lambda 런타임(x86_64 리눅스)용
휠을 받는다. 이걸 빠뜨리면 맥에서 빌드한 pydantic-core 가 Lambda 에서
``No module named 'pydantic_core._pydantic_core'`` 로 죽는다.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
OUT = ROOT / "build" / "lambda"

# Lambda 런타임과 맞춰야 하는 값들 (infra/stacks/dropship_stack.py 참고)
PYTHON_VERSION = "3.12"
PLATFORM = "manylinux2014_x86_64"

# 런타임이 이미 제공하는 것은 넣지 않는다. boto3 는 Lambda 에 기본 포함이라
# 다시 넣으면 패키지가 10MB 넘게 커지기만 한다.
PROVIDED_BY_RUNTIME = {"boto3", "botocore"}


def dependencies() -> list[str]:
    """pyproject 의 런타임 의존성에서 Lambda 에 넣을 것만 고른다."""
    import tomllib

    with (API_DIR / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)

    picked = []
    for spec in data["project"]["dependencies"]:
        name = spec.split(">")[0].split("=")[0].split("[")[0].strip().lower()
        if name not in PROVIDED_BY_RUNTIME:
            picked.append(spec)
    return picked


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    specs = dependencies()
    print(f"의존성 {len(specs)}개를 {PLATFORM} 용으로 설치합니다:")
    for spec in specs:
        print(f"  - {spec}")

    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--target", str(OUT),
            "--platform", PLATFORM,
            "--python-version", PYTHON_VERSION,
            "--implementation", "cp",
            # 소스 배포판을 받으면 빌드 머신 아키텍처로 컴파일돼 Lambda 에서
            # 깨진다. 반드시 휠만 받는다.
            "--only-binary=:all:",
            "--upgrade",
            "--quiet",
            *specs,
        ],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print("의존성 설치 실패", file=sys.stderr)
        return result.returncode

    # 앱 코드를 얹는다. 핸들러 경로는 app.main.lambda_handler 이므로
    # 패키지 루트에 app/ 이 그대로 있어야 한다.
    shutil.copytree(
        API_DIR / "app",
        OUT / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # 용량 줄이기.
    #
    # ``.dist-info`` 는 **지우지 않는다.** python-ulid 처럼 임포트 시점에
    # ``importlib.metadata`` 로 자기 버전을 읽는 라이브러리가 있어서, 메타데이터를
    # 지우면 ``No package metadata was found for ...`` 로 Lambda 가 기동조차 못 한다.
    # 아끼는 용량은 수백 KB 뿐이고 잃는 것은 서비스 전체다.
    for pattern in ("**/__pycache__", "**/tests"):
        for path in OUT.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\n완료: {OUT} ({total / 1024 / 1024:.1f}MB)")

    return verify(specs)


def verify(specs: list[str]) -> int:
    """패키지가 **자기 힘으로** 임포트되는지 확인한다.

    그냥 ``python -c "import app.main"`` 으로는 부족하다. 빌드 머신의 venv 에도
    같은 라이브러리가 깔려 있어서, 패키지에서 빠진 모듈이 venv 에서 대신 로드되면
    검증은 통과하고 Lambda 에서만 죽는다. 그래서 로드된 모듈의 경로가 실제로
    패키지 안인지까지 확인한다.
    """
    # 검증은 Lambda 환경을 흉내 내야 하므로 런타임이 제공하는 boto3 가 필요하다.
    # 없으면 "패키지가 잘못됐다" 가 아니라 "검증 환경이 덜 갖춰졌다" 이므로
    # 원인을 구분해서 알려준다.
    try:
        import boto3  # noqa: F401
    except ImportError:
        print(
            "검증하려면 boto3 가 필요합니다 (Lambda 런타임이 제공하는 것을 흉내 냄).\n"
            "  pip install boto3",
            file=sys.stderr,
        )
        return 1

    # 메타데이터가 실제로 남아 있는지 파일 시스템에서 직접 본다.
    missing = [
        spec
        for spec in specs
        if not list(OUT.glob(f"{_dist_name(spec)}-*.dist-info"))
        and not list(OUT.glob(f"{_dist_name(spec).replace('-', '_')}-*.dist-info"))
    ]
    if missing:
        print(f"패키지 메타데이터 누락: {missing}", file=sys.stderr)
        return 1

    probe = """
import pathlib, sys
out = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(out))

import app.main
assert app.main.lambda_handler
import app.sweeper
assert app.sweeper.lambda_handler

# 각 모듈이 venv 가 아니라 패키지 안에서 왔는지 확인한다.
# boto3/botocore 는 Lambda 런타임이 제공하므로 일부러 뺐고, 검사 대상도 아니다.
for name in ("fastapi", "pydantic", "pydantic_settings", "mangum", "ulid", "cryptography"):
    module = __import__(name)
    path = pathlib.Path(module.__file__).resolve()
    if out not in path.parents:
        raise SystemExit(f"{name} 이(가) 패키지 밖에서 로드됨: {path}")

import importlib.metadata as md
md.version("python-ulid")   # 임포트 시점에 메타데이터를 읽는다
print("검증 통과")
"""
    check = subprocess.run(
        [sys.executable, "-c", probe, str(OUT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        print("패키지 검증 실패:\n" + check.stdout + check.stderr, file=sys.stderr)
        return 1
    print(check.stdout.strip())
    return 0


def _dist_name(spec: str) -> str:
    return spec.split(">")[0].split("=")[0].split("[")[0].strip()


if __name__ == "__main__":
    sys.exit(main())
