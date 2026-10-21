import type { ReactNode } from 'react';

interface NoticeProps {
  tone?: 'info' | 'success' | 'warning' | 'error';
  title?: string;
  children: ReactNode;
  onClose?: () => void;
}

/** 区块内提示条：用于错误、成功与警示信息 */
export function Notice({ tone = 'info', title, children, onClose }: NoticeProps) {
  return (
    <div className={`notice notice--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <div className="notice__body">
        {title && <strong className="notice__title">{title}</strong>}
        <div className="notice__text">{children}</div>
      </div>
      {onClose && (
        <button type="button" className="notice__close" onClick={onClose} aria-label="关闭提示">
          ×
        </button>
      )}
    </div>
  );
}
