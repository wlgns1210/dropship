import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";

// ESLint 9 는 flat config 를 기본으로 쓰지만 eslint-config-next 는 아직
// eslintrc 형식이라 FlatCompat 로 감싸 준다.
const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

export default [
  { ignores: [".next/**", "out/**", "node_modules/**"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // QR 이미지는 외부 API 가 그려주는 PNG 라 next/image 로 최적화할 대상이 아니다.
      // 정적 내보내기라 이미지 최적화 서버 자체가 없다.
      "@next/next/no-img-element": "off",
    },
  },
];
