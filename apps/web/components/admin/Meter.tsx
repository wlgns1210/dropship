"use client";

import { utilization } from "@/lib/admin";
import { ko } from "@/lib/messages";

interface Props {
  label: string;
  percent: number | null;
  /** 퍼센트 아래에 붙는 설명. 예: "1.2GB / 1.9GB" */
  detail?: string;
}

/**
 * 한계 대비 비율을 보여주는 미터.
 *
 * 두 조각짜리 원그래프 대신 같은 트랙 위의 막대를 쓴다. 비율 하나를 읽는 데
 * 각도를 비교하게 만들 이유가 없다.
 *
 * 상태 글자(여유·주의·높음·위험)를 항상 함께 낸다. 색만으로 상태를 표현하면
 * 색각 이상 사용자가 읽을 수 없고, 라이트 배경에서는 '주의'·'높음' 단계의
 * 대비가 3:1 미만이라 색 자체도 약하다.
 */
export function Meter({ label, percent, detail }: Props) {
  if (percent === null) {
    return (
      <div className="tile">
        <div className="tile-label">{label}</div>
        <div className="tile-value" style={{ color: "var(--text-faint)" }}>
          —
        </div>
        <div className="tile-sub">{ko.admin.unavailable}</div>
      </div>
    );
  }

  const { color, label: state } = utilization(percent);
  const width = Math.max(0, Math.min(100, percent));

  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="meter-head">
        <span className="meter-pct">{percent.toFixed(1)}%</span>
        <span className="meter-state" style={{ color }}>
          {state}
        </span>
      </div>
      <div
        className="meter-track"
        role="meter"
        aria-valuenow={Math.round(percent)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} ${percent.toFixed(1)}퍼센트, ${state}`}
      >
        <div className="meter-fill" style={{ width: `${width}%`, background: color }} />
      </div>
      {detail && <div className="tile-sub">{detail}</div>}
    </div>
  );
}
