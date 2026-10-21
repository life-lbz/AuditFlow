// 与后端 API 契约一一对应的类型定义

export type Role = 'employee' | 'reviewer' | 'admin';

export type CaseCategory = 'travel' | 'meal' | 'office_supplies';

export type DeclaredDocumentType =
  | 'invoice'
  | 'receipt'
  | 'bank_statement'
  | 'approval_email';

export interface SystemInfo {
  model_provider: string;
  storage_backend: string;
  mock_erp_url: string;
  minio_bucket?: string;
  statuses: string[];
  terminal_statuses: string[];
  cases: {
    total: number;
    by_status: Record<string, number>;
  };
  policy_chunks: number;
  budgets: {
    agent_max_tool_calls: number;
    agent_max_replans: number;
    agent_max_steps: number;
    case_timeout_secs: number;
  };
  retrieval: {
    keyword_k: number;
    vector_k: number;
    fusion_k: number;
    rerank_k: number;
  };
  risk_thresholds: {
    low: number;
    high: number;
  };
}

export interface ModelHealth {
  provider: string;
  model: string;
  ok: boolean;
  note: string;
  prompt_version?: string;
}

export interface ErpHealth {
  ok: boolean;
  url: string;
}

export interface CaseOut {
  id: string;
  employee_id: string;
  category: string;
  amount: number;
  description: string;
  status: string;
  risk_level: string | null;
  state_version: number;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentPage {
  page_no: number;
  ocr_text: string;
  image_key: string | null;
  image_url: string | null;
}

export interface DocumentField {
  field_name: string;
  field_label: string;
  value: string;
  confidence: number;
  page_no: number | null;
  bbox: unknown;
}

export interface CaseDocument {
  id: string;
  type: string;
  type_label: string;
  storage_key: string;
  sha256: string;
  page_count: number;
  created_at: string;
  pages: DocumentPage[];
  fields: DocumentField[];
}

export interface ExtractedField {
  value: string;
  confidence: number;
  label: string;
  document_id: string;
}

export interface EvidenceItem {
  id: string;
  source_type: string;
  source_id: string;
  page_no: number | null;
  bbox: unknown;
  quote: string;
  score: number;
}

export interface PolicyEvidence extends EvidenceItem {
  citation: string;
}

export interface CheckItem {
  id: string;
  rule_name: string;
  rule_version: string;
  status: string;
  severity: string;
  message: string;
  evidence_ids: string[];
  input_json: Record<string, unknown>;
  result_json: Record<string, unknown>;
  created_at: string;
}

export interface DecisionOut {
  id: string;
  decision: string;
  confidence: number;
  risk_level: string;
  reason: string;
  evidence_ids: string[];
  created_at: string;
}

export interface ApprovalOut {
  id: string;
  case_id?: string;
  reviewer_id: string;
  action: string;
  comment: string;
  created_at: string;
}

export interface ErpTransaction {
  id: string;
  erp_id: string | null;
  status: string;
  idempotency_key: string;
  result_json: Record<string, unknown> | null;
}

export interface CaseDetail extends CaseOut {
  documents: CaseDocument[];
  extracted_fields: Record<string, ExtractedField>;
  evidence: EvidenceItem[];
  policy_evidence: PolicyEvidence[];
  checks: CheckItem[];
  decision: DecisionOut | null;
  approvals: ApprovalOut[];
  erp_transaction: ErpTransaction | null;
  trace_url: string;
  allowed_events: string[];
  missing_documents: string[];
}

export interface RunRequest {
  auto_submit: boolean;
  mode: string;
}

export interface RunDecision {
  decision?: string;
  risk_level?: string;
  confidence?: number;
  summary?: string;
  evidence_ids?: string[];
  failed_checks?: string[];
  missing_items?: string[];
  proposed_actions?: string[];
}

export interface RunResponse {
  case_id: string;
  status: string;
  detail: string;
  decision: RunDecision;
  risk_level: string;
  risk_score: number;
  agent_used: boolean;
  agent_triggers: string[];
  rule_failures: string[];
  verifier_status: string;
  verifier_reason: string;
  needs_approval: boolean;
  auto_submit_eligible: boolean;
  erp: Record<string, unknown> | null;
  transitions: string[];
  errors: string[];
}

export interface UploadDocumentResponse {
  document_id: string;
  document_type: string;
  page_count: number;
  ocr_backend: string;
  ocr_degraded: boolean;
  fields: Record<string, { value: string; confidence: number }>;
  missing_fields: string[];
  errors: string[];
  case_status: string;
}

export interface TraceEvent {
  id: string;
  trace_id: string;
  case_id: string;
  span_id: string | null;
  parent_span_id: string | null;
  event_type: string;
  actor: string | null;
  tool_name: string | null;
  model: string | null;
  prompt_version: string | null;
  duration_ms: number | null;
  error_code: string | null;
  payload_json: Record<string, unknown>;
  created_at: string;
}

export interface ReplayFieldValue {
  value: string | number;
  confidence: number;
}

export interface ReplayTimelineItem {
  seq: number;
  at: string | null;
  event_type: string;
  actor: string | null;
  duration_ms: number | null;
  summary: string;
  transition?: Record<string, unknown>;
  fields?: Record<string, ReplayFieldValue>;
  citations?: unknown;
  decision?: Record<string, unknown>;
}

export interface ReplayResponse {
  case_id: string;
  final_status: string;
  risk_level: string | null;
  event_count: number;
  timeline: ReplayTimelineItem[];
  note: string;
}

export interface ApprovalResult {
  case_id: string;
  action: string;
  status: string;
  detail: string;
  erp?: Record<string, unknown> | null;
  errors?: string[];
}

export interface PolicySummary {
  policy_id: string;
  version: string;
  effective_date: string;
  expiry_date: string | null;
  chunk_count: number;
}

export interface EvalTagMetric {
  count: number;
  decision_accuracy: number;
  avg_tool_steps: number;
}

export interface EvalFailure {
  case_id: string;
  expected: string;
  actual: string;
  expected_risk: string;
  actual_risk: string;
  tags: string[];
  verifier: string;
  errors: string[];
  attribution: string;
}

export interface EvalFieldError {
  case_id: string;
  field: string;
  expected: unknown;
  actual: unknown;
  note?: string;
}

export interface EvalMetrics {
  total: number;
  decision_accuracy: number;
  risk_accuracy: number;
  error_auto_approve_rate: number;
  task_success_rate: number;
  human_takeover_rate: number;
  agent_usage_rate: number;
  avg_tool_steps: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  field_precision: number;
  field_recall: number;
  field_f1: number;
  policy_recall_at_5: number;
  evidence_coverage: number;
  evidence_precision: number;
  by_tag: Record<string, EvalTagMetric>;
  failures: EvalFailure[];
  field_errors: EvalFieldError[];
}

export interface EvalOutcome {
  case_id: string;
  expected: string;
  actual: string;
  expected_risk: string;
  actual_risk: string;
  status: string;
  agent_used: boolean;
  tool_calls: number;
  latency_ms: number;
  auto_submitted: boolean;
  verifier: string;
  tags: string[];
}

export interface EvalRunResponse {
  suite: string;
  mode: string;
  started_at: string;
  finished_at: string;
  duration_secs: number;
  metrics: EvalMetrics;
  outcomes: EvalOutcome[];
  errors: string[];
}

export interface EvalRunSummary {
  id: string;
  suite: string;
  agent_mode: string;
  metrics: EvalMetrics;
  created_at: string;
}

export interface EvalCaseItem {
  id: string;
  category: string | null;
  amount: number | null;
  description: string | null;
  expected: Record<string, unknown>;
  tags: string[];
}
