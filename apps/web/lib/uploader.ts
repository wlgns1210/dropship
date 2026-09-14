/**
 * 브라우저 → S3 직접 멀티파트 업로드.
 *
 * 이 파일이 프로젝트에서 가장 손이 많이 가는 곳이다. 유의할 점 네 가지:
 *
 * 1. **fetch 가 아니라 XMLHttpRequest 를 쓴다.** fetch 에는 업로드 진행률 이벤트가
 *    없다. 1GB 를 올리는 동안 진행 막대가 안 움직이면 사용자는 멈춘 줄 안다.
 *
 * 2. **ETag 는 CORS 로 열어줘야 읽힌다.** 브라우저는 교차 출처 응답에서 극소수
 *    헤더만 JS 에 노출한다. 버킷 CORS 의 ExposeHeaders 에 ETag 가 없으면 업로드는
 *    성공하는데 멀티파트를 닫을 수 없다.
 *
 * 3. **파트는 메모리에 통째로 올리지 않는다.** File.slice() 는 Blob 참조만 만들고
 *    실제 읽기는 XHR 이 스트리밍으로 한다. 파일 전체를 ArrayBuffer 로 읽으면
 *    1GB 업로드에서 탭이 죽는다.
 *
 * 4. **동시 실행은 제한한다.** 전 파트를 한꺼번에 띄우면 브라우저 연결 수 제한에
 *    걸려 오히려 느려지고, 모바일에서는 메모리가 터진다.
 */

import type { CompletedPart, FileUpload } from "./api";

/** 동시에 올릴 파트 수. 브라우저의 호스트당 연결 한도(보통 6)보다 낮게 잡는다. */
const CONCURRENCY = 4;

/** 파트 단위 재시도 횟수. 일시적인 네트워크 오류를 흡수한다. */
const MAX_RETRIES = 3;

export interface UploadProgress {
  /** 전체 업로드된 바이트 */
  loaded: number;
  /** 전체 바이트 */
  total: number;
  /** 초당 바이트. 측정 전에는 0 */
  speed: number;
}

export class UploadCanceled extends Error {
  constructor() {
    super("canceled");
    this.name = "UploadCanceled";
  }
}

export class UploadFailed extends Error {
  constructor(readonly filename: string) {
    super(`${filename} 업로드 실패`);
    this.name = "UploadFailed";
  }
}

interface PartTask {
  fileIndex: number;
  filename: string;
  partNumber: number;
  url: string;
  blob: Blob;
}

/**
 * 세션에 담긴 모든 파일을 올리고, complete 요청에 넣을 파트 목록을 돌려준다.
 */
export async function uploadAll(
  files: File[],
  uploads: FileUpload[],
  options: {
    onProgress?: (progress: UploadProgress) => void;
    signal?: AbortSignal;
  } = {},
): Promise<{ file_index: number; parts: CompletedPart[] }[]> {
  const { onProgress, signal } = options;

  const tasks = buildTasks(files, uploads);
  const total = files.reduce((sum, f) => sum + f.size, 0);

  // 파트별 전송량을 따로 들고 전체를 합산한다. 재시도로 같은 파트를 다시 올려도
  // 진행률이 100%를 넘지 않게 하려면 누적이 아니라 덮어쓰기여야 한다.
  const loadedByPart = new Map<string, number>();
  const startedAt = Date.now();

  const report = () => {
    if (!onProgress) return;
    let loaded = 0;
    for (const value of loadedByPart.values()) loaded += value;
    const elapsed = (Date.now() - startedAt) / 1000;
    onProgress({ loaded, total, speed: elapsed > 0.5 ? loaded / elapsed : 0 });
  };

  const results = new Map<number, CompletedPart[]>();
  for (const upload of uploads) results.set(upload.file_index, []);

  await runPool(tasks, CONCURRENCY, async (task) => {
    const taskKey = `${task.fileIndex}:${task.partNumber}`;
    const etag = await putPartWithRetry(task, {
      signal,
      onProgress: (loaded) => {
        loadedByPart.set(taskKey, loaded);
        report();
      },
    });
    // 파트가 끝나면 전송량을 확정값으로 고정한다.
    loadedByPart.set(taskKey, task.blob.size);
    report();

    results.get(task.fileIndex)!.push({ part_number: task.partNumber, etag });
  });

  return uploads.map((upload) => ({
    file_index: upload.file_index,
    // S3 는 파트 번호 순서대로 정렬된 목록을 요구한다.
    parts: (results.get(upload.file_index) ?? []).sort(
      (a, b) => a.part_number - b.part_number,
    ),
  }));
}

function buildTasks(files: File[], uploads: FileUpload[]): PartTask[] {
  const tasks: PartTask[] = [];

  for (const upload of uploads) {
    const file = files[upload.file_index];
    if (!file) {
      throw new Error(`파일 인덱스 ${upload.file_index} 를 찾을 수 없습니다`);
    }

    upload.part_urls.forEach((url, offset) => {
      const start = offset * upload.part_size;
      const end = Math.min(start + upload.part_size, file.size);
      tasks.push({
        fileIndex: upload.file_index,
        filename: file.name,
        partNumber: offset + 1,
        url,
        // slice 는 데이터를 복사하지 않는다. 실제 읽기는 XHR 이 전송하며 한다.
        blob: file.slice(start, end),
      });
    });
  }

  // 큰 파트부터 처리하면 꼬리에 작은 파트만 남아 마무리가 매끄럽다.
  return tasks.sort((a, b) => b.blob.size - a.blob.size);
}

async function putPartWithRetry(
  task: PartTask,
  options: { signal?: AbortSignal; onProgress: (loaded: number) => void },
): Promise<string> {
  let lastError: unknown;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt += 1) {
    if (options.signal?.aborted) throw new UploadCanceled();
    try {
      return await putPart(task, options);
    } catch (error) {
      if (error instanceof UploadCanceled) throw error;
      lastError = error;
      // 재시도 전에 진행률을 되돌린다. 안 그러면 실패한 전송분이 남아
      // 합계가 실제보다 부풀어 오른다.
      options.onProgress(0);
      if (attempt < MAX_RETRIES - 1) {
        await sleep(500 * 2 ** attempt);
      }
    }
  }

  console.error("파트 업로드 실패", task.filename, task.partNumber, lastError);
  throw new UploadFailed(task.filename);
}

function putPart(
  task: PartTask,
  options: { signal?: AbortSignal; onProgress: (loaded: number) => void },
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", task.url, true);

    const onAbort = () => xhr.abort();
    options.signal?.addEventListener("abort", onAbort, { once: true });

    const cleanup = () => options.signal?.removeEventListener("abort", onAbort);

    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) options.onProgress(event.loaded);
    });

    xhr.addEventListener("load", () => {
      cleanup();
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(`S3 가 ${xhr.status} 를 반환했습니다`));
        return;
      }
      const etag = xhr.getResponseHeader("ETag");
      if (!etag) {
        // 거의 항상 버킷 CORS 의 ExposeHeaders 누락이다. 원인을 명시해 둔다.
        reject(
          new Error(
            "ETag 헤더를 읽을 수 없습니다. 버킷 CORS 의 ExposeHeaders 에 ETag 가 있는지 확인하세요.",
          ),
        );
        return;
      }
      resolve(etag);
    });

    xhr.addEventListener("error", () => {
      cleanup();
      reject(new Error("네트워크 오류"));
    });

    xhr.addEventListener("abort", () => {
      cleanup();
      reject(new UploadCanceled());
    });

    // Content-Type 을 지정하지 않는다. presigned URL 서명에 포함되지 않은 헤더를
    // 얹으면 S3 가 SignatureDoesNotMatch 로 거부한다.
    xhr.send(task.blob);
  });
}

/** 동시 실행 수를 제한하는 간단한 작업 풀. */
async function runPool<T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>,
): Promise<void> {
  let cursor = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const item = items[cursor];
      cursor += 1;
      if (item !== undefined) await worker(item);
    }
  });
  // 하나라도 실패하면 즉시 전파한다. 나머지 러너는 다음 반복에서 멈춘다.
  await Promise.all(runners);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
