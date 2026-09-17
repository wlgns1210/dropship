"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Meter } from "@/components/admin/Meter";
import { Tile } from "@/components/admin/Tile";
import { TransferTable } from "@/components/admin/TransferTable";
import {
  type AdminStats,
  deleteTransfer,
  fetchStats,
  fetchTransfers,
  formatClock,
  formatUptime,
  serviceLook,
  tokenStore,
  type TransferPage,
} from "@/lib/admin";
import { formatBytes } from "@/lib/format";
import { ko } from "@/lib/messages";

const REFRESH_MS = 5000;

type View =
  | { kind: "loading" }
  | { kind: "gate"; error?: string }
  | { kind: "disabled" }
  | { kind: "ready"; stats: AdminStats; error?: string };

export default function AdminPage() {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [input, setInput] = useState("");
  const tokenRef = useRef<string | null>(null);

  const load = useCallback(async (token: string) => {
    const result = await fetchStats(token);
    switch (result.kind) {
      case "ok":
        setView({ kind: "ready", stats: result.stats });
        break;
      case "unauthorized":
        tokenStore.clear();
        tokenRef.current = null;
        setView({ kind: "gate", error: ko.admin.wrongToken });
        break;
      case "disabled":
        setView({ kind: "disabled" });
        break;
      case "error":
        // 일시적 오류로 화면을 비우지 않는다. 직전 값을 유지하고 배너만 띄운다.
        setView((current) =>
          current.kind === "ready"
            ? { ...current, error: result.message }
            : { kind: "gate", error: result.message },
        );
    }
  }, []);

  useEffect(() => {
    const saved = tokenStore.read();
    if (!saved) {
      setView({ kind: "gate" });
      return;
    }
    tokenRef.current = saved;
    void load(saved);

    const timer = setInterval(() => {
      if (tokenRef.current) void load(tokenRef.current);
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const token = input.trim();
    if (!token) return;
    tokenStore.write(token);
    tokenRef.current = token;
    setInput("");
    setView({ kind: "loading" });
    void load(token);
  }

  function signOut() {
    tokenStore.clear();
    tokenRef.current = null;
    setView({ kind: "gate" });
  }

  if (view.kind === "loading") {
    return (
      <main className="card">
        <p className="center-meta">{ko.admin.loading}</p>
      </main>
    );
  }

  if (view.kind === "disabled") {
    return (
      <main className="card">
        <div className="empty">
          <h1>{ko.admin.disabled}</h1>
          <p>{ko.admin.disabledHint}</p>
        </div>
      </main>
    );
  }

  if (view.kind === "gate") {
    return (
      <main className="card">
        <h1 className="section-title">{ko.admin.signIn}</h1>
        <p className="section-sub">{ko.admin.signInHint}</p>
        {view.error && <div className="notice notice-error">{view.error}</div>}
        <form className="token-form" onSubmit={submit}>
          <input
            className="token-input"
            type="password"
            autoComplete="off"
            placeholder={ko.admin.tokenPlaceholder}
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
          <button type="submit" className="btn" disabled={!input.trim()}>
            {ko.admin.enter}
          </button>
        </form>
      </main>
    );
  }

  return (
    <Dashboard
      stats={view.stats}
      error={view.error}
      onSignOut={signOut}
      token={tokenRef.current ?? ""}
    />
  );
}

function Dashboard({
  stats,
  error,
  onSignOut,
  token,
}: {
  stats: AdminStats;
  error?: string;
  onSignOut: () => void;
  token: string;
}) {
  const { system, disk, files, services, transfers, activity, storage } = stats;
  const memory = system.memory;
  const sweep = stats.last_sweep;

  const [page, setPage] = useState<TransferPage | null>(null);
  const [limit, setLimit] = useState(25);

  // 목록은 지표와 따로 가져온다. 파일명·공유 코드가 담겨 오는 민감한 응답이라
  // 5초 폴링에 섞지 않고, 화면에 들어올 때와 변경이 있을 때만 부른다.
  const reloadList = useCallback(async () => {
    if (!token) return;
    setPage(await fetchTransfers(token, { limit }));
  }, [token, limit]);

  useEffect(() => {
    void reloadList();
  }, [reloadList]);

  const removeTransfer = useCallback(
    async (code: string) => {
      const ok = await deleteTransfer(token, code);
      if (ok) await reloadList();
      return ok;
    },
    [token, reloadList],
  );

  return (
    <main className="admin-shell">
      <div className="admin-head">
        <h1 className="admin-title">{ko.admin.title}</h1>
        <span className="admin-updated">
          {ko.admin.updated(formatClock(system.now))}
          {" · "}
          <button type="button" className="iconbtn" onClick={onSignOut}>
            {ko.admin.signOut}
          </button>
        </span>
      </div>

      {error && <div className="notice notice-warn">{error}</div>}

      {/* ── 시스템 ── 한계 대비 비율이라 미터가 맞다 */}
      <section className="admin-section">
        <h2>{ko.admin.system}</h2>
        <div className="admin-grid">
          <Meter
            label={ko.admin.memory}
            percent={memory?.percent ?? null}
            detail={
              memory ? `${formatBytes(memory.used)} / ${formatBytes(memory.total)}` : undefined
            }
          />
          <Meter
            label={ko.admin.disk}
            percent={disk?.percent ?? null}
            detail={disk ? `${formatBytes(disk.free)} 남음` : undefined}
          />
          <Tile
            label={ko.admin.accepting}
            value={
              stats.accepting_uploads === null
                ? "—"
                : stats.accepting_uploads
                  ? ko.admin.acceptingYes
                  : ko.admin.acceptingNo
            }
            // 거절 중일 때만 색을 준다. 평상시 숫자는 본문 잉크를 유지한다.
            accent={
              stats.accepting_uploads === false ? "var(--status-critical)" : undefined
            }
            sub={ko.admin.acceptingNoHint(formatBytes(stats.disk_headroom_bytes))}
          />
          <Meter
            label={ko.admin.cpu}
            percent={system.cpu_percent}
            detail={
              system.load
                ? `${ko.admin.load} ${system.load.join(" / ")} · ${system.cpu_count}코어`
                : undefined
            }
          />
          <Tile
            label={ko.admin.uptime}
            value={system.uptime !== null ? formatUptime(system.uptime) : "—"}
            sub={
              memory && memory.swap_total > 0
                ? `${ko.admin.swap} ${formatBytes(memory.swap_used)} / ${formatBytes(memory.swap_total)}`
                : undefined
            }
          />
        </div>
      </section>

      {/* ── 서비스 ── 점 + 글자. 색만으로 상태를 읽게 하지 않는다 */}
      <section className="admin-section">
        <h2>{ko.admin.services}</h2>
        <div className="admin-grid">
          {Object.entries(services).map(([unit, state]) => {
            const look = serviceLook(state);
            return (
              <div key={unit} className="badge-row">
                <span className="badge-name" title={unit}>
                  {unit.replace(/\.(service|timer)$/, "")}
                </span>
                <span className="badge" style={{ color: look.color }}>
                  <span className="badge-dot" style={{ background: look.color }} aria-hidden />
                  {look.label}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── 전송 ── 비교 대상 없는 단일 값들이라 타일 */}
      {transfers && (
        <section className="admin-section">
          <h2>{ko.admin.transfers}</h2>
          <div className="admin-grid">
            <Tile
              label={ko.admin.live}
              value={transfers.live}
              sub={storage ? formatBytes(storage.tracked_bytes) : undefined}
            />
            <Tile label={ko.admin.pending} value={transfers.pending} />
            <Tile label={ko.admin.expiring} value={transfers.expiring_1h} />
            <Tile
              label={ko.admin.overdue}
              value={transfers.overdue}
              // 이 수가 0이 아니면 Sweeper 가 뒤처졌다는 뜻이고, 방치하면
              // 디스크가 찬다. 숫자 자체보다 "왜 위험한가"를 같이 보여준다.
              accent={transfers.overdue > 0 ? "var(--status-serious)" : undefined}
              sub={transfers.overdue > 0 ? ko.admin.overdueWarn : undefined}
            />
            {files && (
              <Tile
                label={ko.admin.storedFiles}
                value={files.files.toLocaleString("ko-KR")}
                sub={formatBytes(files.bytes)}
              />
            )}
            <Tile
              label={ko.admin.sweeper}
              value={sweep ? formatClock(sweep.at) : "—"}
              sub={
                sweep
                  ? ko.admin.sweeperResult(sweep.transfers, sweep.objects)
                  : ko.admin.sweeperNever
              }
            />
          </div>
        </section>
      )}

      {/* ── 오늘 ── */}
      {activity && (
        <section className="admin-section">
          <h2>{ko.admin.activity}</h2>
          <div className="admin-grid">
            <Tile label={ko.admin.uploadsToday} value={activity.uploads_today} />
            <Tile label={ko.admin.bytesToday} value={formatBytes(activity.bytes_today)} />
            <Tile label={ko.admin.ipsToday} value={activity.unique_ips_today} />
            <Tile label={ko.admin.downloadsTotal} value={activity.downloads_total} />
          </div>
        </section>
      )}

      {/* ── 업로드 목록 ── 항목이 많고 각각 의미가 있어 표가 맞다 */}
      {page && (
        <section className="admin-section">
          <h2>{ko.admin.list}</h2>
          <TransferTable
            rows={page.items}
            total={page.total}
            now={system.now}
            onDelete={removeTransfer}
            onLoadMore={() => setLimit((current) => current + 25)}
          />
        </section>
      )}
    </main>
  );
}
