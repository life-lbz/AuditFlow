import { statusLabel } from '../format';

const STATUS_TONES: Record<string, string> = {
  CREATED: 'slate',
  INGESTED: 'slate',
  EXTRACTED: 'blue',
  EVIDENCE_READY: 'blue',
  VALIDATED: 'cyan',
  DECIDED: 'indigo',
  VERIFIED: 'cyan',
  WAITING_APPROVAL: 'amber',
  EXECUTING: 'amber',
  COMPLETED: 'green',
  NEEDS_INFO: 'orange',
  ESCALATED: 'rose',
  FAILED: 'red',
  ROLLED_BACK: 'red',
  REJECTED: 'red',
};

interface StatusBadgeProps {
  status: string;
  showDot?: boolean;
}

/** 案件状态徽章：按生命周期阶段分配颜色 */
export function StatusBadge({ status, showDot = true }: StatusBadgeProps) {
  const tone = STATUS_TONES[status] ?? 'slate';
  return (
    <span className={`badge badge--${tone}`} title={status}>
      {showDot && <span className="badge__dot" />}
      {statusLabel(status)}
    </span>
  );
}
