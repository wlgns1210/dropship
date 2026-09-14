import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // S3 + CloudFront 로 내보낼 정적 사이트. SSR 서버를 두지 않는다.
  //
  // 공유 링크(/oslo/113245)는 빌드 시점에 알 수 없으므로 Next 의 동적 라우트로는
  // 만들 수 없다. 대신 app/not-found.tsx 가 수신 화면 역할을 한다:
  //   - 개발 서버   : 매칭되지 않는 경로 → not-found 렌더
  //   - CloudFront : 404 → /404.html 을 200 으로 응답하도록 설정
  // 양쪽에서 완전히 같은 코드가 돈다.
  output: "export",

  // 정적 호스팅이라 Next 의 이미지 최적화 서버가 없다.
  images: { unoptimized: true },

  // 빌드를 타입 오류에서 멈추게 둔다. 조용히 깨진 채 배포되는 것보다 낫다.
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: false },
};

export default nextConfig;
