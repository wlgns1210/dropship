"""서버 상태 수집. 의존성 없이 ``/proc`` 과 표준 라이브러리만 쓴다.

psutil 을 넣지 않은 이유: 여기서 필요한 건 메모리·CPU·디스크·업타임 네 가지뿐이고,
리눅스에서는 전부 ``/proc`` 에 텍스트로 있다. 배포 패키지를 키우고 네이티브 휠
의존성을 늘릴 만한 일이 아니다.

리눅스가 아니면(개발용 맥·윈도우) 조용히 None 을 돌려준다. 관리자 페이지가
로컬에서 뜨지 않는 것보다 일부 칸이 비는 편이 낫다.
"""

import asyncio
import contextlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

_PROC = Path("/proc")


def _read_proc(name: str) -> str | None:
    try:
        return (_PROC / name).read_text()
    except OSError:
        return None


# ── 메모리 ────────────────────────────────────────────────────


def memory() -> dict[str, Any] | None:
    """총량·사용량·가용량(바이트).

    ``MemFree`` 가 아니라 **MemAvailable** 을 쓴다. 리눅스는 남는 메모리를
    페이지 캐시로 채우기 때문에 MemFree 는 거의 항상 작게 나오고, 그걸로
    사용률을 계산하면 멀쩡한 서버가 늘 90% 를 넘는 것처럼 보인다.
    MemAvailable 은 캐시 중 회수 가능한 몫을 반영한 값이다.
    """
    raw = _read_proc("meminfo")
    if raw is None:
        return None

    fields: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        value = rest.strip().split(" ")[0]
        if value.isdigit():
            fields[key] = int(value) * 1024  # meminfo 단위는 kB

    total = fields.get("MemTotal")
    available = fields.get("MemAvailable")
    if not total or available is None:
        return None

    return {
        "total": total,
        "available": available,
        "used": total - available,
        "percent": round((total - available) / total * 100, 1),
        "swap_total": fields.get("SwapTotal", 0),
        "swap_used": fields.get("SwapTotal", 0) - fields.get("SwapFree", 0),
    }


# ── CPU ───────────────────────────────────────────────────────


def _cpu_times() -> tuple[int, int] | None:
    """(유휴, 전체) 누적 틱."""
    raw = _read_proc("stat")
    if raw is None:
        return None
    for line in raw.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            # 4번째(idle)와 5번째(iowait)가 놀고 있는 시간이다
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return idle, sum(values)
    return None


async def cpu_percent(sample_seconds: float = 0.12) -> float | None:
    """두 시점의 누적 틱 차이로 사용률을 낸다.

    ``/proc/stat`` 은 부팅 이후 누적값이라 한 번만 읽으면 "부팅 이후 평균" 이
    나온다. 지금 상태를 보려면 짧은 간격으로 두 번 읽어 차분해야 한다.
    """
    first = _cpu_times()
    if first is None:
        return None
    await asyncio.sleep(sample_seconds)
    second = _cpu_times()
    if second is None:
        return None

    idle_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round((1 - idle_delta / total_delta) * 100, 1)


def load_average() -> list[float] | None:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def cpu_count() -> int:
    return os.cpu_count() or 1


# ── 업타임 ────────────────────────────────────────────────────


def uptime_seconds() -> int | None:
    raw = _read_proc("uptime")
    if raw is None:
        return None
    try:
        return int(float(raw.split()[0]))
    except (ValueError, IndexError):
        return None


# ── 디스크 ────────────────────────────────────────────────────


def disk(path: str | Path) -> dict[str, Any] | None:
    """파일 서비스에서 가장 중요한 지표다. 차면 업로드가 통째로 실패한다."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return None
    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
    }


def directory_size(path: Path, *, max_entries: int = 50_000) -> dict[str, int]:
    """디렉터리의 파일 수와 합계 크기.

    ``os.scandir`` 로 직접 순회한다. 파일이 비정상적으로 많아졌을 때 관리자
    페이지 요청 하나가 서버를 붙잡지 않도록 상한을 둔다.
    """
    count = 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if count >= max_entries:
                        return {"files": count, "bytes": total, "truncated": 1}
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        count += 1
                        with contextlib.suppress(OSError):
                            total += entry.stat().st_size
        except OSError:
            continue
    return {"files": count, "bytes": total, "truncated": 0}


# ── systemd ───────────────────────────────────────────────────


def service_state(unit: str) -> str:
    """``systemctl is-active`` 결과. 조회 실패는 'unknown'.

    루트가 아니어도 상태 조회는 된다. 실패해도 예외를 올리지 않는 이유는,
    관리자 페이지가 이 한 칸 때문에 500 으로 죽으면 정작 볼 수 없기 때문이다.
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def collect_static() -> dict[str, Any]:
    """CPU 샘플링이 필요 없는 항목만 모은다."""
    return {
        "memory": memory(),
        "load": load_average(),
        "cpu_count": cpu_count(),
        "uptime": uptime_seconds(),
        "now": int(time.time()),
    }
