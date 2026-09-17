/** 관리자 API 클라이언트와 지표 타입. */

import { ko } from "./messages";

const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");
const TOKEN_KEY = "skiff.admin.token";

export interface Meter {
  total: number;
  used: number;
  percent: number;
}

export interface AdminStats {
  deploy_mode: string;
  system: {
    memory: (Meter & { available: number; swap_total: number; swap_used: number }) | null;
    cpu_percent: number | null;
    load: number[] | null;
    cpu_count: number;
    uptime: number | null;
    now: number;
  };
  disk: (Meter & { free: number }) | null;
  files: { files: number; bytes: number; truncated: number } | null;
  services: Record<string, string>;
  policy: { max_total_bytes: number; daily_quota_bytes: number };
  transfers?: {
    ready: number;
    pending: number;
    live: number;
    expiring_1h: number;
    overdue: number;
  };
  storage?: { tracked_bytes: number };
  activity?: {
    downloads_total: number;
    uploads_today: number;
    bytes_today: number;
    unique_ips_today: number;
    quota_bytes_today: number;
  };
  last_sweep?: { at: number; transfers: number; objects: number; counters: number } | null;
}

export interface TransferRow {
  code: string;
  status: string;
  total_size: number;
  created_at: number;
  expires_at: number;
  download_count: number;
  /** IP 해시의 앞 8자. 원본 IP 는 복원되지 않는다. */
  uploader: string;
  files: { index: number; name: string; size: number; mime: string }[];
}

export interface TransferPage {
  items: TransferRow[];
  total: number;
  offset: number;
  limit: number;
}

export type FetchResult =
  | { kind: "ok"; stats: AdminStats }
  | { kind: "unauthorized" }
  | { kind: "disabled" }
  | { kind: "error"; message: string };

/** 토큰은 이 브라우저에만 둔다. 서버는 저장하지 않는다. */
export const tokenStore = {
  read(): string | null {
    try {
      return window.localStorage.getItem(TOKEN_KEY);
    } catch {
      // 사생활 보호 모드 등에서 접근이 막힐 수 있다.
      return null;
    }
  },
  write(token: string): void {
    try {
      window.localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* 저장에 실패해도 이번 세션에서는 동작한다 */
    }
  },
  clear(): void {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* 무시 */
    }
  },
};

export async function fetchStats(token: string): Promise<FetchResult> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/admin/stats`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    return { kind: "error", message: ko.errors.network };
  }

  // 라우터 자체가 등록되지 않은 서버 — 관리자 기능이 꺼져 있다는 뜻이다.
  if (response.status === 404) return { kind: "disabled" };
  if (response.status === 401) return { kind: "unauthorized" };
  if (response.status === 429) return { kind: "error", message: ko.errors.rateLimited };
  if (!response.ok) return { kind: "error", message: `HTTP ${response.status}` };

  return { kind: "ok", stats: (await response.json()) as AdminStats };
}

/**
 * 업로드된 전송 목록.
 *
 * **여기에는 파일명과 공유 코드가 담겨 온다.** 지표 조회(fetchStats)와 달리
 * 민감하므로 화면에서도 필요할 때만 부른다.
 */
export async function fetchTransfers(
  token: string,
  options: { limit?: number; status?: string } = {},
): Promise<TransferPage | null> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.status) params.set("status", options.status);

  try {
    const response = await fetch(`${BASE}/api/admin/transfers?${params}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as TransferPage;
  } catch {
    return null;
  }
}

/** 관리자 강제 삭제. 소유자 토큰 없이 지운다. */
export async function deleteTransfer(token: string, code: string): Promise<boolean> {
  try {
    const response = await fetch(`${BASE}/api/admin/transfers/${code}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * 사용률을 네 단계로 나눈다.
 *
 * 반환하는 라벨이 중요하다. 상태색은 라이트 표면에서 대비가 낮은 단계가 있고,
 * 무엇보다 색각 이상 사용자에게는 색만으로 구분되지 않는다. 그래서 미터 옆에
 * **항상 글자를 함께** 내보낸다.
 */
export function utilization(percent: number): { color: string; label: string } {
  if (percent >= 95) return { color: "var(--status-critical)", label: ko.admin.stateCritical };
  if (percent >= 85) return { color: "var(--status-serious)", label: ko.admin.stateHigh };
  if (percent >= 70) return { color: "var(--status-warning)", label: ko.admin.stateWarn };
  return { color: "var(--status-good)", label: ko.admin.stateOk };
}

export function serviceLook(state: string): { color: string; label: string } {
  switch (state) {
    case "active":
      return { color: "var(--status-good)", label: ko.admin.serviceActive };
    case "failed":
      return { color: "var(--status-critical)", label: ko.admin.serviceFailed };
    case "inactive":
      return { color: "var(--status-serious)", label: ko.admin.serviceInactive };
    default:
      return { color: "var(--text-faint)", label: ko.admin.serviceUnknown };
  }
}

/** 가동 시간을 한국어로. 큰 단위 두 개까지만 보여준다. */
export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return hours > 0 ? `${days}일 ${hours}시간` : `${days}일`;
  if (hours > 0) return minutes > 0 ? `${hours}시간 ${minutes}분` : `${hours}시간`;
  return `${minutes}분`;
}

export function formatClock(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
