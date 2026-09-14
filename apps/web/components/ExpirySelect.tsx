"use client";

import { ko } from "@/lib/messages";

interface Props {
  value: number;
  choices: number[];
  onChange: (seconds: number) => void;
  disabled?: boolean;
}

export function ExpirySelect({ value, choices, onChange, disabled = false }: Props) {
  return (
    <div className="row">
      <span className="label" id="expiry-label">
        {ko.upload.expiryLabel}
      </span>
      <div className="segmented" role="group" aria-labelledby="expiry-label">
        {choices.map((seconds) => (
          <button
            key={seconds}
            type="button"
            aria-pressed={seconds === value}
            disabled={disabled}
            onClick={() => onChange(seconds)}
          >
            {ko.expiryOptions[seconds] ?? `${seconds}초`}
          </button>
        ))}
      </div>
    </div>
  );
}
