"use client";

interface Props {
  label: string;
  value: string | number;
  sub?: string;
  /** 주의를 끌어야 하는 값에만 쓴다. 평상시 숫자는 본문 잉크를 유지한다. */
  accent?: string;
}

/**
 * 현재값 하나를 보여주는 타일.
 *
 * 막대 하나짜리 차트를 그리지 않는다. 비교 대상이 없는 단일 값에 축과 격자를
 * 붙이면 읽는 비용만 늘어난다.
 */
export function Tile({ label, value, sub, accent }: Props) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value" style={accent ? { color: accent } : undefined}>
        {value}
      </div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  );
}
