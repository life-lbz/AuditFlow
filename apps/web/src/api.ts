// API 客户端：统一注入 X-Role / X-User-Id（dev 模式）或 Bearer Token（token 模式）

import type {
  ApprovalOut,
  ApprovalResult,
  CaseDetail,
  CaseOut,
  EvalCaseItem,
  EvalRunResponse,
  EvalRunSummary,
  ErpHealth,
  ModelHealth,
  PolicySummary,
  ReplayResponse,
  Role,
  RunRequest,
  RunResponse,
  SystemInfo,
  TraceEvent,
  UploadDocumentResponse,
} from './types';

const RAW_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
export const API_BASE = RAW_BASE.replace(/\/+$/, '');

let currentRole: Role = 'employee';
let currentUserId = 'u_1002';

const TOKEN_KEY = 'auditflow.token';
let accessToken = window.localStorage.getItem(TOKEN_KEY) || '';

/** 设置/清除访问令牌；token 鉴权模式下后端会忽略 X-Role */
export function setAccessToken(token: string): void {
  accessToken = token.trim();
  if (accessToken) {
    window.localStorage.setItem(TOKEN_KEY, accessToken);
  } else {
    window.localStorage.removeItem(TOKEN_KEY);
  }
}

export function getAccessToken(): string {
  return accessToken;
}

/** 同步当前身份，供所有请求头使用 */
export function setApiIdentity(role: Role, userId: string): void {
  currentRole = role;
  currentUserId = userId;
}

export function getApiIdentity(): { role: Role; userId: string } {
  return { role: currentRole, userId: currentUserId };
}

/** 将后端返回的相对路径拼接为完整 API 地址 */
export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  formData?: FormData;
}

function extractDetail(data: unknown): string {
  if (!data || typeof data !== 'object') return '';
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (item && typeof item === 'object') {
        const record = item as { msg?: unknown; loc?: unknown };
        const loc = Array.isArray(record.loc) ? record.loc.join('.') : '';
        const msg = typeof record.msg === 'string' ? record.msg : JSON.stringify(item);
        return loc ? `${loc}: ${msg}` : msg;
      }
      return String(item);
    });
    return messages.join('；');
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail);
  }
  return '';
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    'X-Role': currentRole,
    'X-User-Id': currentUserId,
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method: options.method ?? 'GET',
      headers,
      body,
    });
  } catch (error) {
    throw new ApiError(
      `无法连接后端 API（${API_BASE}）：${error instanceof Error ? error.message : String(error)}`,
      0,
    );
  }

  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      data = text;
    }
  }

  if (!response.ok) {
    const detail = extractDetail(data);
    const fallback =
      typeof data === 'string' && data.trim() ? data.trim().slice(0, 300) : response.statusText;
    throw new ApiError(
      detail || `请求失败（HTTP ${response.status}）：${fallback || '未知错误'}`,
      response.status,
    );
  }

  return data as T;
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  });
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const api = {
  // 系统
  health: () => request<{ status: string }>('/health'),
  systemInfo: () => request<SystemInfo>('/system/info'),
  systemModel: () => request<ModelHealth>('/system/model'),
  systemErp: () => request<ErpHealth>('/system/erp'),

  // 案件
  createCase: (body: {
    employee_id: string;
    category: string;
    amount: number;
    description: string;
  }) => request<CaseOut>('/cases', { method: 'POST', body }),
  listCases: (params: { status?: string; limit?: number; offset?: number } = {}) =>
    request<CaseOut[]>(`/cases${query(params)}`),
  getCase: (caseId: string) => request<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`),
  uploadDocument: (caseId: string, file: File, declaredType?: string) => {
    const form = new FormData();
    form.append('file', file);
    if (declaredType) form.append('declared_type', declaredType);
    return request<UploadDocumentResponse>(
      `/cases/${encodeURIComponent(caseId)}/documents`,
      { method: 'POST', formData: form },
    );
  },
  runCase: (caseId: string, body: RunRequest) =>
    request<RunResponse>(`/cases/${encodeURIComponent(caseId)}/run`, {
      method: 'POST',
      body,
    }),
  retryCase: (caseId: string, body: RunRequest) =>
    request<RunResponse>(`/cases/${encodeURIComponent(caseId)}/retry`, {
      method: 'POST',
      body,
    }),
  getTrace: (caseId: string) =>
    request<TraceEvent[]>(`/cases/${encodeURIComponent(caseId)}/trace`),
  getReplay: (caseId: string) =>
    request<ReplayResponse>(`/cases/${encodeURIComponent(caseId)}/replay`),

  // 审批
  pendingApprovals: () => request<CaseOut[]>('/approvals/pending'),
  listApprovals: (caseId: string) =>
    request<ApprovalOut[]>(`/cases/${encodeURIComponent(caseId)}/approvals`),
  submitApproval: (caseId: string, action: string, comment: string) =>
    request<ApprovalResult>(`/cases/${encodeURIComponent(caseId)}/approvals`, {
      method: 'POST',
      body: { action, comment },
    }),

  // 制度
  listPolicies: () => request<{ policies: PolicySummary[] }>('/policies'),

  // 评测
  runEval: (body: {
    suite: string;
    mode: string;
    limit: number;
    auto_submit: boolean;
    tags?: string[] | null;
  }) => request<EvalRunResponse>('/eval/runs', { method: 'POST', body }),
  listEvalRuns: () => request<{ runs: EvalRunSummary[] }>('/eval/runs'),
  listEvalCases: (params: { suite?: string; limit?: number; offset?: number } = {}) =>
    request<{ suite: string; total: number; cases: EvalCaseItem[] }>(
      `/eval/cases${query(params)}`,
    ),
};
