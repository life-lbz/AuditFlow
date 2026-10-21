interface LoadingProps {
  label?: string;
  compact?: boolean;
}

/** 统一的加载态：旋转指示 + 文案 */
export function Loading({ label = '正在加载…', compact = false }: LoadingProps) {
  return (
    <div className={`loading${compact ? ' loading--compact' : ''}`} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span className="loading__label">{label}</span>
    </div>
  );
}
