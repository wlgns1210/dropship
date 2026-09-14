"use client";

import { useEffect, useState } from "react";

import { ReceiveView } from "@/components/ReceiveView";
import { ko } from "@/lib/messages";

/**
 * 수신 화면이 여기 있는 이유.
 *
 * 공유 링크(`/oslo/113245`)는 빌드 시점에 알 수 없어서 정적 내보내기(output: export)
 * 에서는 Next 의 동적 라우트로 만들 수 없다 (generateStaticParams 가 미리 모든 경로를
 * 알아야 한다). 그래서 "매칭되지 않는 경로" 처리기를 수신 화면으로 쓴다:
 *
 *   개발 서버   — 라우트가 없는 경로면 Next 가 이 컴포넌트를 렌더한다
 *   CloudFront — 404 를 `/404.html` 로, 상태 코드는 200 으로 바꿔 응답하게 설정
 *
 * 양쪽에서 완전히 같은 코드가 돈다. CloudFront Function 도, 서버도 필요 없다.
 */

// 서버가 인정하는 코드 형식과 같아야 한다 (app/services/codes.py 참고).
const CODE_PATTERN = /^\/([a-z]{3,12})\/(\d{6})\/?$/;

export default function NotFound() {
  // 정적 HTML 하나로 모든 경로를 처리하므로 경로 판별은 마운트 후에 한다.
  // 서버 렌더 결과와 어긋나지 않게 첫 렌더에서는 아무것도 확정하지 않는다.
  const [code, setCode] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const match = CODE_PATTERN.exec(window.location.pathname);
    if (match) setCode(`${match[1]}/${match[2]}`);
    setChecked(true);
  }, []);

  if (!checked) {
    return (
      <main className="card">
        <p className="center-meta">{ko.receive.preparing}</p>
      </main>
    );
  }

  if (code) return <ReceiveView code={code} />;

  return (
    <main className="card">
      <div className="empty">
        <h1>{ko.notFound.title}</h1>
        {/* next/link 가 아니라 <a> 인 것은 의도적이다. 이 화면은 라우트 매니페스트에
            없는 경로(404.html 이 임의 경로로 서빙된 상태)에서 렌더되므로, 클라이언트
            사이드 전환보다 전체 페이지 이동이 확실하다. 막다른 길에서 한 번 눌리는
            버튼이라 SPA 전환의 이점도 없다. */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a className="btn" href="/">
          {ko.notFound.home}
        </a>
      </div>
    </main>
  );
}
