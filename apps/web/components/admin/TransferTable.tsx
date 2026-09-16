"use client";

import { useState } from "react";

import type { TransferRow } from "@/lib/admin";
import { formatBytes, formatRemaining } from "@/lib/format";
import { ko } from "@/lib/messages";

interface Props {
  rows: TransferRow[];
  total: number;
  now: number;
  onDelete: (code: string) => Promise<boolean>;
  onLoadMore?: () => void;
}

/**
 * 업로드된 전송 목록.
 *
 * 항목마다 의미 있는 값이 여섯 개 이상이라 차트가 아니라 **표**가 맞다.
 * 세로로 비교되는 숫자 열에는 tabular-nums 를 걸어 자릿수가 어긋나지 않게 한다.
 */
export function TransferTable({ rows, total, now, onDelete, onLoadMore }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function remove(code: string) {
    if (!window.confirm(ko.admin.deleteConfirm(code))) return;
    setBusy(code);
    setError(null);
    const ok = await onDelete(code);
    if (!ok) setError(ko.admin.deleteFailed);
    setBusy(null);
  }

  if (rows.length === 0) {
    return <p className="center-meta">{ko.admin.listEmpty}</p>;
  }

  return (
    <>
      {error && <div className="notice notice-error">{error}</div>}

      <div className="table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>{ko.admin.colCode}</th>
              <th>{ko.admin.colFiles}</th>
              <th className="num">{ko.admin.colSize}</th>
              <th className="num">{ko.admin.colExpires}</th>
              <th className="num">{ko.admin.colDownloads}</th>
              <th>{ko.admin.colUploader}</th>
              <th aria-label={ko.admin.deleteOne} />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const remaining = row.expires_at - now;
              const first = row.files[0];
              const extra = row.files.length - 1;

              return (
                <tr key={row.code}>
                  <td>
                    <code className="cell-code">{row.code}</code>
                    {row.status === "pending" && (
                      <span className="cell-tag">{ko.admin.statusPending}</span>
                    )}
                  </td>
                  <td>
                    <span className="cell-name" title={row.files.map((f) => f.name).join(", ")}>
                      {first ? first.name : "—"}
                    </span>
                    {extra > 0 && <span className="cell-muted">{ko.admin.moreFiles(extra)}</span>}
                  </td>
                  <td className="num">{formatBytes(row.total_size)}</td>
                  <td className="num">
                    {remaining > 0 ? (
                      formatRemaining(remaining)
                    ) : (
                      // 만료됐는데 아직 남아 있다는 뜻이다. Sweeper 가 곧 지운다.
                      <span style={{ color: "var(--status-serious)" }}>{ko.admin.expired}</span>
                    )}
                  </td>
                  <td className="num">{row.download_count}</td>
                  <td>
                    <code className="cell-muted">{row.uploader}</code>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={busy !== null}
                      onClick={() => remove(row.code)}
                    >
                      {busy === row.code ? ko.admin.deleting : ko.admin.deleteOne}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="tile-sub">{ko.admin.showing(rows.length, total)}</span>
        {onLoadMore && rows.length < total && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onLoadMore}>
            {ko.admin.more}
          </button>
        )}
      </div>
    </>
  );
}
