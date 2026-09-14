"use client";

import { formatBytes } from "@/lib/format";
import { ko } from "@/lib/messages";

interface Props {
  files: File[];
  onRemove?: (index: number) => void;
}

export function FileList({ files, onRemove }: Props) {
  if (files.length === 0) return null;

  return (
    <ul className="filelist">
      {files.map((file, index) => (
        // 이름이 같은 파일을 여러 개 담을 수 있으므로 이름만으로는 키가 안 된다.
        <li key={`${file.name}:${file.size}:${file.lastModified}:${index}`} className="fileitem">
          <span className="fileitem-name" title={file.name}>
            {file.name}
          </span>
          <span className="fileitem-size">{formatBytes(file.size)}</span>
          {onRemove && (
            <button
              type="button"
              className="iconbtn"
              onClick={() => onRemove(index)}
              aria-label={`${file.name} ${ko.upload.remove}`}
            >
              ✕
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
