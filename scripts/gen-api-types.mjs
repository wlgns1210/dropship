/**
 * FastAPI 의 OpenAPI 스키마에서 TypeScript 타입을 생성한다.
 *
 *   make dev-api        # 다른 터미널에서 API 를 띄워 두고
 *   node scripts/gen-api-types.mjs
 *
 * Next.js + FastAPI 조합의 유일한 실질 비용은 타입을 두 언어에서 따로 관리하는
 * 것이다. 이 스크립트가 그 비용을 없앤다. CI 는 생성 결과가 커밋된 것과 같은지
 * 검사해서, Pydantic 모델만 고치고 프론트를 안 고친 상태로 머지되는 것을 막는다.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const API_URL = process.env.API_URL ?? "http://localhost:8000";
const OUT = "apps/web/lib/api-types.ts";

const response = await fetch(`${API_URL}/openapi.json`).catch(() => null);

if (!response?.ok) {
  console.error(`OpenAPI 스키마를 가져오지 못했습니다: ${API_URL}/openapi.json`);
  console.error("먼저 `make dev-api` 로 API 를 띄워 주세요.");
  process.exit(1);
}

const schemaPath = join(mkdtempSync(join(tmpdir(), "skiff-")), "openapi.json");
writeFileSync(schemaPath, JSON.stringify(await response.json()));

execFileSync(
  process.platform === "win32" ? "npx.cmd" : "npx",
  ["--yes", "openapi-typescript@7", schemaPath, "-o", OUT],
  { stdio: "inherit" },
);

console.log(`생성 완료: ${OUT}`);
