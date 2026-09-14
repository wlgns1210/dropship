"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ko } from "@/lib/messages";

interface Props {
  onAdd: (files: File[]) => void;
  disabled?: boolean;
}

export function Dropzone({ onAdd, disabled = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState(false);

  // 드래그 이벤트는 자식 요소를 지날 때마다 enter/leave 가 번갈아 발생한다.
  // 깊이를 세지 않으면 드롭존 안에서 마우스를 움직일 때 테두리가 깜빡인다.
  const depth = useRef(0);

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      depth.current = 0;
      setActive(false);
      if (disabled) return;
      const dropped = Array.from(event.dataTransfer.files);
      if (dropped.length > 0) onAdd(dropped);
    },
    [disabled, onAdd],
  );

  // 붙여넣기로 스크린샷을 바로 보내는 경로. 이게 있으면 캡처 → 공유가 두 동작이다.
  useEffect(() => {
    if (disabled) return;
    const onPaste = (event: ClipboardEvent) => {
      const pasted = Array.from(event.clipboardData?.files ?? []);
      if (pasted.length > 0) {
        event.preventDefault();
        onAdd(pasted);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [disabled, onAdd]);

  return (
    <>
      <button
        type="button"
        className="dropzone"
        data-active={active}
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragEnter={(event) => {
          event.preventDefault();
          depth.current += 1;
          setActive(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => {
          event.preventDefault();
          depth.current -= 1;
          if (depth.current <= 0) setActive(false);
        }}
        onDrop={handleDrop}
      >
        <div className="dropzone-title">{ko.upload.dropHere}</div>
        <div className="dropzone-sub">{ko.upload.orBrowse}</div>
        <div className="dropzone-hint">{ko.upload.pasteHint}</div>
      </button>

      <input
        ref={inputRef}
        type="file"
        multiple
        className="sr-only"
        onChange={(event) => {
          const picked = Array.from(event.target.files ?? []);
          if (picked.length > 0) onAdd(picked);
          // 같은 파일을 연달아 고를 수 있게 값을 비운다.
          event.target.value = "";
        }}
      />
    </>
  );
}
