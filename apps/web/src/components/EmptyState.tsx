import type { ReactNode } from 'react';

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}

/** 空数据占位：统一的留白与说明 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  compact = false,
}: EmptyStateProps) {
  return (
    <div className={`empty-state${compact ? ' empty-state--compact' : ''}`}>
      <div className="empty-state__icon" aria-hidden="true">
        {icon ?? (
          <svg viewBox="0 0 48 48" width="40" height="40">
            <rect x="9" y="7" width="30" height="34" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
            <path d="M16 17h16M16 24h16M16 31h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        )}
      </div>
      <div className="empty-state__title">{title}</div>
      {description && <p className="empty-state__desc">{description}</p>}
      {action && <div className="empty-state__action">{action}</div>}
    </div>
  );
}
