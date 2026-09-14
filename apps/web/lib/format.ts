/** 한국어 표기 규칙에 맞춘 포매터. */

const UNITS = ["B", "KB", "MB", "GB"] as const;

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0B";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // 1KB 미만은 소수점이 의미 없고, 큰 단위는 한 자리면 충분하다.
  const digits = unit === 0 ? 0 : value < 10 ? 1 : 0;
  return `${value.toFixed(digits)}${UNITS[unit]}`;
}

/**
 * 남은 시간을 사람이 읽는 말로. 큰 단위 두 개까지만 보여준다.
 * "2일 3시간" 은 쓸모 있지만 "2일 3시간 12분 5초" 는 아니다.
 */
export function formatRemaining(seconds: number): string {
  if (seconds <= 0) return "0분";

  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (days > 0) return hours > 0 ? `${days}일 ${hours}시간` : `${days}일`;
  if (hours > 0) return minutes > 0 ? `${hours}시간 ${minutes}분` : `${hours}시간`;
  if (minutes > 0) return `${minutes}분`;
  return "1분 미만";
}

/** 업로드 속도 표기. 0으로 나누는 것을 막는다. */
export function formatSpeed(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return "";
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function formatPercent(loaded: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(100, Math.round((loaded / total) * 100));
}
