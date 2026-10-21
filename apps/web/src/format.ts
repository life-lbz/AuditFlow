// 展示层格式化工具：状态/分类/风险/动作的中文标签与时间、金额格式

export const STATUS_LABELS: Record<string, string> = {
  CREATED: '已创建',
  INGESTED: '已入库',
  EXTRACTED: '已抽取',
  EVIDENCE_READY: '证据就绪',
  VALIDATED: '已校验',
  DECIDED: '已决策',
  VERIFIED: '已复核',
  WAITING_APPROVAL: '待审核',
  EXECUTING: '执行中',
  COMPLETED: '已完成',
  NEEDS_INFO: '待补材料',
  ESCALATED: '已升级',
  FAILED: '失败',
  ROLLED_BACK: '已回滚',
  REJECTED: '已驳回',
};

export const CATEGORY_LABELS: Record<string, string> = {
  travel: '差旅',
  meal: '餐饮',
  office_supplies: '办公用品',
};

export const RISK_LABELS: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
};

export const DECISION_LABELS: Record<string, string> = {
  approve: '通过',
  auto_approve: '自动通过',
  reject: '驳回',
  escalate: '升级人工',
  request_info: '要求补材料',
  needs_info: '需补充信息',
  modify_fields: '修改字段',
};

export const ACTION_LABELS: Record<string, string> = {
  approve: '批准并提交 ERP',
  reject: '驳回',
  request_info: '要求补材料',
  modify_fields: '修改字段后重跑',
  terminate: '终止',
};

export const CHECK_STATUS_LABELS: Record<string, string> = {
  passed: '通过',
  failed: '未通过',
  skipped: '跳过',
};

export const SEVERITY_LABELS: Record<string, string> = {
  none: '无',
  low: '低',
  medium: '中',
  high: '高',
  critical: '严重',
};

export const RULE_LABELS: Record<string, string> = {
  invoice_required_fields: '发票必填字段',
  amount_matches_receipt: '发票与收据金额一致',
  amount_matches_bank: '发票与流水金额一致',
  date_in_range: '票据日期在行程内',
  duplicate_invoice: '重复报销检测',
  category_limit: '类别费用额度',
  vendor_check: '供应商白名单',
  approval_required: '审批有效性',
};

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  invoice: '发票',
  receipt: '收据',
  bank_statement: '银行流水',
  approval_email: '审批邮件',
  unknown: '未知类型',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

export function riskLabel(risk: string | null | undefined): string {
  if (!risk) return '未评估';
  return RISK_LABELS[risk] ?? risk;
}

export function decisionLabel(decision: string | undefined): string {
  if (!decision) return '—';
  return DECISION_LABELS[decision] ?? decision;
}

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? rule;
}

export function checkStatusLabel(status: string): string {
  return CHECK_STATUS_LABELS[status] ?? status;
}

export function severityLabel(severity: string): string {
  return SEVERITY_LABELS[severity] ?? severity;
}

export function documentTypeLabel(type: string | null): string {
  if (!type) return DOCUMENT_TYPE_LABELS.unknown;
  return DOCUMENT_TYPE_LABELS[type] ?? type;
}

export function formatCurrency(amount: number): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
  }).format(amount);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function formatClock(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

export function formatPercent(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function truncateMiddle(value: string, head = 8, tail = 6): string {
  if (value.length <= head + tail + 3) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

export function formatAny(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function shortJson(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
