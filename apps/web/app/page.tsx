"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Dropzone } from "@/components/Dropzone";
import { ExpirySelect } from "@/components/ExpirySelect";
import { FileList } from "@/components/FileList";
import { ShareResult } from "@/components/ShareResult";
import { ApiError, api, shareUrlFor, type ClientConfig } from "@/lib/api";
import { formatBytes, formatPercent, formatSpeed } from "@/lib/format";
import { ko } from "@/lib/messages";
import { UploadCanceled, uploadAll, type UploadProgress } from "@/lib/uploader";

/** 서버 /api/config 를 못 받았을 때 쓰는 값. 서버 정책과 같게 유지한다. */
const FALLBACK_CONFIG: ClientConfig = {
  max_total_bytes: 1024 ** 3,
  max_files: 100,
  expiry_choices: [3600, 21600, 86400, 259200, 604800],
  daily_quota_bytes: 5 * 1024 ** 3,
};

type Phase = "idle" | "creating" | "uploading" | "finishing";

interface Share {
  code: string;
  shareUrl: string;
  ownerToken: string;
  expiresAt: number;
}

export default function UploadPage() {
  const [config, setConfig] = useState<ClientConfig>(FALLBACK_CONFIG);
  const [files, setFiles] = useState<File[]>([]);
  const [expiresIn, setExpiresIn] = useState(86400);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<UploadProgress>({ loaded: 0, total: 0, speed: 0 });
  const [error, setError] = useState<string | null>(null);
  const [share, setShare] = useState<Share | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    // 상한·보관 기간 선택지를 서버에서 받아온다. 실패해도 폴백으로 동작한다.
    api.config().then(setConfig).catch(() => undefined);
  }, []);

  const busy = phase !== "idle";
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);
  const overLimit = totalSize > config.max_total_bytes;

  const addFiles = useCallback(
    (incoming: File[]) => {
      setError(null);
      setFiles((current) => {
        const merged = [...current, ...incoming];
        if (merged.length > config.max_files) {
          setError(ko.errors.tooMany(config.max_files));
          return merged.slice(0, config.max_files);
        }
        return merged;
      });
    },
    [config.max_files],
  );

  function removeFile(index: number) {
    setFiles((current) => current.filter((_, i) => i !== index));
    setError(null);
  }

  function reset() {
    setFiles([]);
    setShare(null);
    setError(null);
    setPhase("idle");
    setProgress({ loaded: 0, total: 0, speed: 0 });
  }

  function cancel() {
    abortRef.current?.abort();
  }

  async function send() {
    if (files.length === 0 || overLimit) return;

    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setPhase("creating");

    try {
      // 1. 업로드 세션을 연다. 여기서 공유 코드와 파트별 presigned URL 을 받는다.
      const session = await api.createTransfer({
        files: files.map((file) => ({
          name: file.name,
          size: file.size,
          mime: file.type || "application/octet-stream",
        })),
        expires_in: expiresIn,
      });

      // 2. 파일은 S3 로 직접 올라간다. 이 바이트는 백엔드를 지나지 않는다.
      setPhase("uploading");
      setProgress({ loaded: 0, total: totalSize, speed: 0 });
      const completed = await uploadAll(files, session.uploads, {
        onProgress: setProgress,
        signal: controller.signal,
      });

      // 3. 확정. 이 요청이 끝나야 공유 링크가 살아난다.
      setPhase("finishing");
      const done = await api.completeTransfer(session.code, {
        owner_token: session.owner_token,
        files: completed,
      });

      setShare({
        code: done.code,
        shareUrl: shareUrlFor(done.code),
        ownerToken: session.owner_token,
        expiresAt: done.expires_at,
      });
    } catch (err) {
      setError(describeError(err));
    } finally {
      abortRef.current = null;
      setPhase("idle");
    }
  }

  if (share) {
    return (
      <main className="card">
        <ShareResult {...share} onReset={reset} />
      </main>
    );
  }

  const percent = formatPercent(progress.loaded, progress.total);

  return (
    <main className="card">
      <Dropzone onAdd={addFiles} disabled={busy} />

      <FileList files={files} onRemove={busy ? undefined : removeFile} />

      {files.length > 0 && (
        <div className="row">
          <span className="label">
            {ko.upload.fileCount(files.length)} · {formatBytes(totalSize)}
          </span>
          {!busy && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={reset}>
              {ko.upload.clear}
            </button>
          )}
        </div>
      )}

      <ExpirySelect
        value={expiresIn}
        choices={config.expiry_choices}
        onChange={setExpiresIn}
        disabled={busy}
      />

      {overLimit && <div className="notice notice-error">{ko.errors.tooLarge}</div>}
      {error && <div className="notice notice-error">{error}</div>}

      {phase === "uploading" && (
        <>
          <div className="progress">
            <div className="progress-bar" style={{ width: `${percent}%` }} />
          </div>
          <div className="progress-meta">
            <span>
              {formatBytes(progress.loaded)} / {formatBytes(progress.total)}
            </span>
            <span>
              {percent}% {formatSpeed(progress.speed)}
            </span>
          </div>
        </>
      )}

      <button
        type="button"
        className="btn btn-block"
        disabled={files.length === 0 || overLimit || busy}
        onClick={send}
      >
        {phaseLabel(phase)}
      </button>

      {busy && (
        <div className="row" style={{ justifyContent: "center" }}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={cancel}>
            {ko.upload.cancel}
          </button>
        </div>
      )}
    </main>
  );
}

function phaseLabel(phase: Phase): string {
  switch (phase) {
    case "creating":
      return ko.upload.preparing;
    case "uploading":
      return ko.upload.sending;
    case "finishing":
      return ko.upload.finishing;
    default:
      return ko.upload.send;
  }
}

function describeError(err: unknown): string {
  if (err instanceof UploadCanceled) return ko.errors.canceled;
  if (err instanceof ApiError) {
    if (err.status === 0) return ko.errors.network;
    if (err.isRateLimited) return err.detail || ko.errors.rateLimited;
    // 서버가 한국어 메시지를 내려주므로 그대로 보여준다.
    return err.detail || ko.errors.generic;
  }
  if (err instanceof Error && err.message) return err.message;
  return ko.errors.generic;
}
