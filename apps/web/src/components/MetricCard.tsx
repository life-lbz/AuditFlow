import type { ReactNode } from 'react';

export type MetricTone = 'default' | 'good' | 'warn' | 'danger' | 'info';

interface MetricCardProps {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: MetricTone;
  /** 0-1 的进度，传入后显示进度条 */
  progress?: number;
  /** 是否使用环形指示器 */
  ring?: boolean;
  footer?: ReactNode;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** 指标卡：支持数值、进度条与环形指标三种形态 */
export function MetricCard({
  label,
  value,
  hint,
  tone = 'default',
  progress,
  ring = false,
  footer,
}: MetricCardProps) {
  const ratio = progress === undefined ? null : clamp01(progress);
  const circumference = 2 * Math.PI * 26;

  return (
    <div className={`metric-card metric-card--${tone}${ring ? ' metric-card--ring' : ''}`}>
      <div className="metric-card__body">
        {ring && ratio !== null && (
          <svg className="metric-ring" viewBox="0 0 64 64" aria-hidden="true">
            <circle className="metric-ring__track" cx="32" cy="32" r="26" />
            <circle
              className="metric-ring__value"
              cx="32"
              cy="32"
              r="26"
              strokeDasharray={`${circumference * ratio} ${circumference}`}
            />
          </svg>
        )}
        <div className="metric-card__text">
          <div className="metric-card__label">{label}</div>
          <div className="metric-card__value">{value}</div>
          {hint && <div className="metric-card__hint">{hint}</div>}
        </div>
      </div>
      {ratio !== null && !ring && (
        <div className="progress progress--thin">
          <span className="progress__bar" style={{ width: `${ratio * 100}%` }} />
        </div>
      )}
      {footer && <div className="metric-card__footer">{footer}</div>}
    </div>
  );
}
