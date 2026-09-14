"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api, type TransferInfo } from "@/lib/api";
import { formatBytes, formatRemaining } from "@/lib/format";
import { ko } from "@/lib/messages";

type State =
  | { status: "loading" }
  | { status: "ready"; transfer: TransferInfo }
  | { status: "gone" }
  | { status: "error"; message: string };

export function ReceiveView({ code }: { code: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [busyIndex, setBusyIndex] = useState<number | null>(null);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));

  useEffect(() => {
    let alive = true;
    api
      .getTransfer(code)
      .then((transfer) => alive && setState({ status: "ready", transfer }))
      .catch((err: unknown) => {
        if (!alive) return;
        if (err instanceof ApiError && err.isGone) {
          setState({ status: "gone" });
        } else {
          setState({
            status: "error",
            message: err instanceof ApiError ? err.detail : ko.errors.generic,
          });
        }
      });
    return () => {
      alive = false;
    };
  }, [code]);

  // 남은 시간을 1분마다 갱신한다. 횟수 제한이 없으니 사용자에게는
  // 이 링크가 언제까지 유효한지가 유일한 단서다.
  useEffect(() => {
    const timer = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 60_000);
    return () => clearInterval(timer);
  }, []);

  const download = useCallback(
    async (index: number) => {
      setBusyIndex(index);
      try {
        const { url } = await api.downloadUrl(code, index);
        // 서명 URL 로 직접 이동시킨다. 파일 바이트는 API 를 거치지 않는다.
        window.location.href = url;
      } catch (err) {
        if (err instanceof ApiError && err.isGone) setState({ status: "gone" });
        else alert(err instanceof ApiError ? err.detail : ko.errors.generic);
      } finally {
        setBusyIndex(null);
      }
    },
    [code],
  );

  if (state.status === "loading") {
    return (
      <main className="card">
        <p className="center-meta">{ko.receive.preparing}</p>
      </main>
    );
  }

  if (state.status === "gone") {
    return (
      <main className="card">
        <div className="empty">
          <h1>{ko.gone.title}</h1>
          <p>{ko.gone.body}</p>
          {/* not-found.tsx 와 같은 이유로 <a>. 이 컴포넌트는 라우트 매니페스트에
              없는 공유 코드 경로에서 렌더된다. */}
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
          <a className="btn" href="/">
            {ko.gone.home}
          </a>
        </div>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="card">
        <div className="notice notice-error">{state.message}</div>
      </main>
    );
  }

  const { transfer } = state;
  const remaining = transfer.expires_at - now;
  const hasRisky = transfer.files.some((file) => file.risky);

  return (
    <main className="card">
      <h1 className="section-title">{ko.receive.title}</h1>
      <p className="section-sub">
        {ko.receive.totalSize(formatBytes(transfer.total_size))} ·{" "}
        {remaining > 0 ? ko.receive.expiresIn(formatRemaining(remaining)) : ko.receive.expired}
      </p>

      {hasRisky && <div className="notice notice-warn">{ko.receive.riskyWarning}</div>}

      <ul className="filelist">
        {transfer.files.map((file) => (
          <li key={file.index} className="fileitem">
            <span className="fileitem-name" title={file.name}>
              {file.name}
            </span>
            <span className="fileitem-size">{formatBytes(file.size)}</span>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busyIndex !== null || remaining <= 0}
              onClick={() => download(file.index)}
            >
              {busyIndex === file.index ? ko.receive.preparing : ko.receive.download}
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
