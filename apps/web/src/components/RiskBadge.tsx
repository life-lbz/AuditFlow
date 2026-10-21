import { riskLabel } from '../format';

interface RiskBadgeProps {
  risk: string | null | undefined;
  compact?: boolean;
}

/** 风险等级徽章：低/中/高分别使用绿、琥珀、红 */
export function RiskBadge({ risk, compact = false }: RiskBadgeProps) {
  const tone = risk === 'high' ? 'red' : risk === 'medium' ? 'amber' : risk === 'low' ? 'green' : 'slate';
  return (
    <span
      className={`badge badge--${tone}${compact ? ' badge--compact' : ''}`}
      title={risk ?? '未评估'}
    >
      <span className="badge__dot" />
      {riskLabel(risk)}
    </span>
  );
}
