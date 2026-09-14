"use client";

import QRCode from "qrcode";
import { useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { formatRemaining } from "@/lib/format";
import { ko } from "@/lib/messages";

interface Props {
  code: string;
  shareUrl: string;
  ownerToken: string;
  expiresAt: number;
  onReset: () => void;
}

export function ShareResult({ code, shareUrl, ownerToken, expiresAt, onReset }: Props) {
  const [copied, setCopied] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);

  const remaining = Math.max(0, expiresAt - Math.floor(Date.now() / 1000));

  // QR 은 브라우저에서 그린다. 서버가 그려주면 (1) 자기 공개 도메인을 알아야 하고
  // (2) 임의 URL 의 QR 을 대신 그려주는 피싱 통로가 열린다. 클라이언트 생성은
  // 네트워크 왕복도 없앤다.
  useEffect(() => {
    let alive = true;
    QRCode.toDataURL(shareUrl, { width: 352, margin: 1, errorCorrectionLevel: "M" })
      .then((url) => alive && setQr(url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [shareUrl]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(shareUrl);
    } catch {
      // HTTPS 가 아니거나 권한이 없으면 클립보드 API 가 막힌다.
      // 이럴 때는 최소한 선택은 되게 해준다.
      window.prompt(ko.result.copyLink, shareUrl);
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  async function removeNow() {
    if (!window.confirm(ko.result.deleteConfirm)) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteTransfer(code, ownerToken);
      setDeleted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : ko.errors.generic);
    } finally {
      setDeleting(false);
    }
  }

  if (deleted) {
    return (
      <div className="empty">
        <h1>{ko.result.deleted}</h1>
        <button type="button" className="btn" onClick={onReset}>
          {ko.result.sendAnother}
        </button>
      </div>
    );
  }

  return (
    <div>
      <h1 className="section-title">{ko.result.title}</h1>
      <p className="section-sub">{ko.result.expiresAt(formatRemaining(remaining))}</p>

      <div className="share-code">
        <code>{shareUrl}</code>
        <button type="button" className="btn btn-sm" onClick={copy}>
          {copied ? ko.result.copied : ko.result.copyLink}
        </button>
      </div>

      <div className="qr-frame">
        {qr ? (
          <img src={qr} alt={`${shareUrl} QR 코드`} width={176} height={176} />
        ) : (
          // 자리를 미리 잡아둬 QR 이 그려질 때 레이아웃이 밀리지 않게 한다.
          <div style={{ width: 176, height: 176 }} aria-hidden />
        )}
      </div>
      <p className="center-meta">{ko.result.qrHint}</p>

      {error && <div className="notice notice-error">{error}</div>}
      <div className="notice notice-info">{ko.result.ownerNotice}</div>

      <div className="row">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onReset}>
          {ko.result.sendAnother}
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={removeNow}
          disabled={deleting}
        >
          {deleting ? ko.result.deleting : ko.result.deleteNow}
        </button>
      </div>
    </div>
  );
}
