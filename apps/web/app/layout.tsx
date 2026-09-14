import type { Metadata, Viewport } from "next";
import Link from "next/link";

import { ko } from "@/lib/messages";
import "./globals.css";

export const metadata: Metadata = {
  title: `${ko.brand} — 파일 보내기`,
  description: ko.tagline,
  // 공유 링크가 검색에 걸리거나 메신저에서 미리보기로 펼쳐지면 안 된다.
  // 파일명이 새고, 크롤러가 링크를 건드리게 된다.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfbfa" },
    { media: "(prefers-color-scheme: dark)", color: "#16171a" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <div className="shell">
          <header className="masthead">
            <Link href="/">
              <span className="brand">{ko.brand}</span>
              <span className="tagline">{ko.tagline}</span>
            </Link>
          </header>
          {children}
          <p className="footnote">{ko.safety.notice}</p>
        </div>
      </body>
    </html>
  );
}
