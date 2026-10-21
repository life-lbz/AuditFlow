import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { api, apiUrl, errorMessage } from '../api';
import { EmptyState } from '../components/EmptyState';
import { EvidencePanel } from '../components/EvidencePanel';
import { Loading } from '../components/Loading';
import { Notice } from '../components/Notice';
import { RiskBadge } from '../components/RiskBadge';
import { StatusBadge } from '../components/StatusBadge';
import {
  actionLabel,
  categoryLabel,
  checkStatusLabel,
  decisionLabel,
  documentTypeLabel,
  formatAny,
  formatCurrency,
  formatDateTime,
  formatPercent,
  ruleLabel,
  severityLabel,
  shortJson,
  truncateMiddle,
} from '../format';
import { useRole } from '../role';
import type { CaseDetail, RunResponse, UploadDocumentResponse } from '../types';

const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'ROLLED_BACK', 'REJECTED'];

const RUN_MODES = [
  { value: 'single_agent_verifier', label: '单 Agent + Verifier（默认）' },
  { value: 'fixed_workflow', label: '固定工作流' },
  { value: 'single_agent', label: '单 Agent' },
  { value: 'single_agent_verifier_researcher', label: '单 Agent + Verifier + 检索' },
];

const DECLARED_TYPES = [
  { value: '', label: '自动识别类型' },
  { value: 'invoice', label: '发票' },
  { value: 'receipt', label: '收据' },
  { value: 'bank_statement', label: '银行流水' },
  { value: 'approval_email', label: '审批邮件' },
];

function ConfidenceBar({ value }: { value: number }) {
  const percent = Math.max(0, Math.min(1, value));
  const warn = value < 0.7;
  return (
    <div className="confidence">
      <div className="progress progress--thin">
        <span
          className={`progress__bar${warn ? ' progress__bar--warn' : ''}`}
          style={{ width: `${percent * 100}%` }}
        />
      </div>
      <span className={`confidence__value${warn ? ' confidence__value--warn' : ''}`}>
        {formatPercent(value)}
      </span>
    </div>
  );
}

/** 案件详情：操作区、运行结果、材料字段、证据、规则、决策、审批与 ERP 全景 */
export function CaseDetailPage() {
  const { id = '' } = useParams();
  const location = useLocation();
  const { role } = useRole();
  const flash = (location.state as { flash?: string } | null)?.flash ?? '';

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(flash);

  const [runMode, setRunMode] = useState('single_agent_verifier');
  const [autoSubmit, setAutoSubmit] = useState(false);
  const [runLoading, setRunLoading] = useState(false);
  const [runResult, setRunResult] = useState<RunResponse | null>(null);

  const [declaredType, setDeclaredType] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadDocumentResponse | null>(null);
  const [uploadError, setUploadError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [activeEvidenceId, setActiveEvidenceId] = useState<string | null>(null);

  const loadDetail = useCallback(
    async (showLoading = true) => {
      if (!id) return;
      if (showLoading) setLoading(true);
      try {
        const data = await api.getCase(id);
        setDetail(data);
        setError('');
      } catch (err: unknown) {
        setError(errorMessage(err));
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [id],
  );

  useEffect(() => {
    setDetail(null);
    setRunResult(null);
    setUploadResult(null);
    setActiveEvidenceId(null);
    setNotice(flash);
    void loadDetail(true);
  }, [loadDetail, role, flash]);

  async function handleRun(kind: 'run' | 'retry') {
    if (!id) return;
    setRunLoading(true);
    setError('');
    setNotice('');
    try {
      const payload = { auto_submit: autoSubmit, mode: runMode };
      const result =
        kind === 'run' ? await api.runCase(id, payload) : await api.retryCase(id, payload);
      setRunResult(result);
      setNotice(
        `${kind === 'run' ? '工作流' : '重试'}执行完成，案件状态：${result.status}${
          result.needs_approval ? '，已转入人工审核' : ''
        }`,
      );
      await loadDetail(false);
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setRunLoading(false);
    }
  }

  async function handleUpload() {
    if (!id) return;
    if (!file) {
      setUploadError('请先选择要上传的文件');
      return;
    }
    setUploading(true);
    setUploadError('');
    setNotice('');
    try {
      const result = await api.uploadDocument(id, file, declaredType || undefined);
      setUploadResult(result);
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setNotice(
        `材料上传成功：${documentTypeLabel(result.document_type)}，抽取 ${Object.keys(result.fields).length} 个字段`,
      );
      await loadDetail(false);
      setActiveEvidenceId(null);
    } catch (err: unknown) {
      setUploadError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  if (loading) {
    return (
      <div className="panel">
        <Loading label="正在加载案件详情…" />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="page">
        <Link to="/cases" className="back-link">← 返回案件列表</Link>
        <div className="panel">
          <Notice tone="error" title="案件加载失败">
            {error || '未找到案件'}
          </Notice>
          <div className="form__actions">
            <button type="button" className="button" onClick={() => void loadDetail(true)}>
              重新加载
            </button>
            <Link to="/cases" className="button">返回列表</Link>
          </div>
        </div>
      </div>
    );
  }

  const isTerminal = TERMINAL_STATUSES.includes(detail.status);
  const docTypeLabels: Record<string, string> = {};
  detail.documents.forEach((doc) => {
    docTypeLabels[doc.id] = doc.type_label;
  });

  return (
    <div className="page">
      <Link to="/cases" className="back-link">← 返回案件列表</Link>

      {notice && (
        <Notice tone="success" onClose={() => setNotice('')}>
          {notice}
        </Notice>
      )}
      {error && (
        <Notice tone="error" title="操作失败" onClose={() => setError('')}>
          {error}
        </Notice>
      )}

      <section className="case-hero">
        <div className="case-hero__main">
          <div className="case-hero__title">
            <code className="id-code id-code--lg">{detail.id}</code>
            <StatusBadge status={detail.status} />
            <RiskBadge risk={detail.risk_level} />
          </div>
          <div className="case-hero__meta">
            <div><span>报销人</span><strong>{detail.employee_id}</strong></div>
            <div><span>类型</span><strong>{categoryLabel(detail.category)}</strong></div>
            <div><span>金额</span><strong className="num">{formatCurrency(detail.amount)}</strong></div>
            <div><span>状态版本</span><strong>v{detail.state_version}</strong></div>
            <div><span>创建时间</span><strong>{formatDateTime(detail.created_at)}</strong></div>
            <div><span>更新时间</span><strong>{formatDateTime(detail.updated_at)}</strong></div>
          </div>
          <p className="case-hero__desc">{detail.description || '（未填写报销事由）'}</p>
          <div className="case-hero__trace">
            Trace ID：<code>{detail.trace_id ?? '—'}</code>
          </div>
        </div>
        <div className="case-hero__side">
          <Link className="button" to={`/cases/${detail.id}/trace`}>
            查看 Trace 回放
          </Link>
          <button type="button" className="button" onClick={() => void loadDetail(true)}>
            刷新详情
          </button>
        </div>
      </section>

      {detail.missing_documents.length > 0 && (
        <Notice tone="warning" title="缺少必需材料">
          还需补充：{detail.missing_documents.join('、')}
        </Notice>
      )}

      <section className="panel action-panel">
        <div className="action-panel__controls">
          <label className="field field--inline">
            <span>编排模式</span>
            <select value={runMode} onChange={(event) => setRunMode(event.target.value)}>
              {RUN_MODES.map((mode) => (
                <option key={mode.value} value={mode.value}>{mode.label}</option>
              ))}
            </select>
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={autoSubmit}
              onChange={(event) => setAutoSubmit(event.target.checked)}
            />
            <span>允许低风险自动提交 ERP</span>
          </label>
          <div className="action-panel__buttons">
            <button
              type="button"
              className="button button--primary"
              onClick={() => void handleRun('run')}
              disabled={runLoading || isTerminal}
            >
              {runLoading ? '执行中…' : '运行工作流'}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => void handleRun('retry')}
              disabled={runLoading || isTerminal}
            >
              重试 / 继续
            </button>
          </div>
        </div>
        <div className="action-panel__hint">
          {isTerminal
            ? '案件已进入终态，不可重新运行。'
            : '运行会推进状态机；auto_submit=true 时低风险案件会直接写入 ERP。'}
          {detail.allowed_events.length > 0 && (
            <span className="action-panel__events">
              允许事件：{detail.allowed_events.join(' / ')}
            </span>
          )}
        </div>
        {runLoading && <Loading compact label="工作流执行中，请稍候…" />}
      </section>

      {runResult && (
        <section className="panel run-result">
          <div className="panel__head">
            <div>
              <h3>工作流运行结果</h3>
              <p className="panel__sub">{runResult.detail || `案件状态：${runResult.status}`}</p>
            </div>
            <div className="panel__tools">
              <StatusBadge status={runResult.status} />
              <RiskBadge risk={runResult.risk_level} />
            </div>
          </div>

          <div className="run-kpi-grid">
            <div className="run-kpi">
              <span>决策</span>
              <strong>{decisionLabel(runResult.decision?.decision)}</strong>
              <small>
                置信度 {formatPercent(runResult.decision?.confidence ?? 0)}
              </small>
            </div>
            <div className="run-kpi">
              <span>风险分</span>
              <strong className="num">{runResult.risk_score.toFixed(3)}</strong>
              <div className="progress progress--thin">
                <span
                  className="progress__bar"
                  style={{ width: `${Math.min(1, Math.max(0, runResult.risk_score)) * 100}%` }}
                />
              </div>
            </div>
            <div className="run-kpi">
              <span>Agent 调用</span>
              <strong>{runResult.agent_used ? '是' : '否'}</strong>
              <small>{runResult.agent_triggers.length} 个触发原因</small>
            </div>
            <div className="run-kpi">
              <span>Verifier</span>
              <strong>{runResult.verifier_status || '—'}</strong>
              <small>{runResult.verifier_reason || '无复核说明'}</small>
            </div>
            <div className="run-kpi">
              <span>人工审批</span>
              <strong>{runResult.needs_approval ? '需要' : '无需'}</strong>
              <small>自动提交资格：{runResult.auto_submit_eligible ? '满足' : '不满足'}</small>
            </div>
            <div className="run-kpi">
              <span>ERP</span>
              <strong>{runResult.erp ? '已执行' : '未执行'}</strong>
              <small>{runResult.erp ? shortJson(runResult.erp).slice(0, 60) : '无 ERP 事务'}</small>
            </div>
          </div>

          {runResult.decision?.summary && (
            <div className="run-summary">
              <strong>决策摘要</strong>
              <p>{runResult.decision.summary}</p>
            </div>
          )}

          <div className="run-detail-grid">
            <div>
              <h4>Agent 触发原因</h4>
              {runResult.agent_triggers.length === 0 ? (
                <p className="muted">未触发 Agent</p>
              ) : (
                <div className="chips">
                  {runResult.agent_triggers.map((item) => (
                    <span className="chip chip--warn" key={item}>{item}</span>
                  ))}
                </div>
              )}
              <h4>规则失败项</h4>
              {runResult.rule_failures.length === 0 ? (
                <p className="muted">无规则失败</p>
              ) : (
                <div className="chips">
                  {runResult.rule_failures.map((item) => (
                    <span className="chip chip--danger" key={item}>{ruleLabel(item)}</span>
                  ))}
                </div>
              )}
              {runResult.decision?.proposed_actions && runResult.decision.proposed_actions.length > 0 && (
                <>
                  <h4>建议动作</h4>
                  <div className="chips">
                    {runResult.decision.proposed_actions.map((item) => (
                      <span className="chip" key={item}>{item}</span>
                    ))}
                  </div>
                </>
              )}
            </div>
            <div>
              <h4>状态流转</h4>
              {runResult.transitions.length === 0 ? (
                <p className="muted">无状态变化</p>
              ) : (
                <div className="chips">
                  {runResult.transitions.map((item) => (
                    <span className="chip chip--info" key={item}>{item}</span>
                  ))}
                </div>
              )}
              <h4>ERP 返回</h4>
              <pre className="json-block">{formatAny(runResult.erp)}</pre>
              {runResult.errors.length > 0 && (
                <>
                  <h4>错误信息</h4>
                  <ul className="error-list">
                    {runResult.errors.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </>
              )}
            </div>
          </div>
        </section>
      )}

      <div className="detail-grid">
        <div className="detail-main">
          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>材料与 OCR 文本</h3>
                <p className="panel__sub">{detail.documents.length} 份文档</p>
              </div>
            </div>
            {detail.documents.length === 0 ? (
              <EmptyState
                compact
                title="暂无材料"
                description="在右侧「上传材料」区域上传发票、流水或审批邮件。"
              />
            ) : (
              <div className="doc-list">
                {detail.documents.map((doc) => (
                  <article className="doc-card" key={doc.id}>
                    <div className="doc-card__head">
                      <div className="doc-card__title">
                        <span className="type-tag">{doc.type_label || documentTypeLabel(doc.type)}</span>
                        <code>{doc.id}</code>
                      </div>
                      <div className="doc-card__meta">
                        <span>{doc.page_count} 页</span>
                        <span>{formatDateTime(doc.created_at)}</span>
                      </div>
                    </div>
                    <div className="doc-card__hash" title={doc.sha256}>
                      SHA256 · {truncateMiddle(doc.sha256, 12, 8)}
                    </div>
                    {doc.pages.length === 0 ? (
                      <p className="muted">无页面内容</p>
                    ) : (
                      doc.pages.map((page) => (
                        <div className="ocr-page" key={page.page_no}>
                          <div className="ocr-page__head">
                            <span>第 {page.page_no} 页 OCR</span>
                            {page.image_url ? (
                              <a href={apiUrl(page.image_url)} target="_blank" rel="noreferrer">
                                查看原图 ↗
                              </a>
                            ) : (
                              <span className="muted">无原图</span>
                            )}
                          </div>
                          <pre className="ocr-preview">{page.ocr_text || '（空）'}</pre>
                        </div>
                      ))
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>字段抽取结果</h3>
                <p className="panel__sub">
                  共 {Object.keys(detail.extracted_fields).length} 个字段 · 置信度低于 70% 标记为警示
                </p>
              </div>
            </div>
            {Object.keys(detail.extracted_fields).length === 0 ? (
              <EmptyState compact title="暂无抽取字段" description="上传材料后自动执行 OCR 与字段抽取。" />
            ) : (
              <div className="table-wrap">
                <table className="table table--compact">
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>值</th>
                      <th className="th-progress">置信度</th>
                      <th>来源文档</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(detail.extracted_fields).map(([name, field]) => (
                      <tr key={name}>
                        <td>
                          <span className="field-name">{field.label || name}</span>
                          <code className="field-key">{name}</code>
                        </td>
                        <td className="field-value">{formatAny(field.value)}</td>
                        <td>
                          <ConfidenceBar value={field.confidence} />
                        </td>
                        <td className="muted">
                          {docTypeLabels[field.document_id] ?? truncateMiddle(field.document_id, 6, 4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>证据查看器</h3>
                <p className="panel__sub">点击证据条目，在左侧页面/OCR 中定位引用</p>
              </div>
              <span className="muted">{detail.evidence.length} 条证据</span>
            </div>
            <EvidencePanel
              documents={detail.documents}
              evidence={detail.evidence}
              activeEvidenceId={activeEvidenceId}
              onSelectEvidence={setActiveEvidenceId}
            />
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>制度证据</h3>
                <p className="panel__sub">检索命中的制度条款与打分</p>
              </div>
              <span className="muted">{detail.policy_evidence.length} 条</span>
            </div>
            {detail.policy_evidence.length === 0 ? (
              <EmptyState compact title="暂无制度证据" description="运行工作流后展示制度检索结果。" />
            ) : (
              <div className="policy-list">
                {detail.policy_evidence.map((item) => (
                  <article className="policy-item" key={item.id}>
                    <div className="policy-item__head">
                      <code>{item.citation}</code>
                      <span className={`score${item.score < 0.4 ? ' score--warn' : ''}`}>
                        score {item.score.toFixed(3)}
                      </span>
                    </div>
                    <p>{item.quote}</p>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>规则检查</h3>
                <p className="panel__sub">确定性规则引擎逐条校验结果</p>
              </div>
              <span className="muted">{detail.checks.length} 条规则</span>
            </div>
            {detail.checks.length === 0 ? (
              <EmptyState compact title="暂无规则结果" description="运行工作流后生成规则检查记录。" />
            ) : (
              <div className="table-wrap">
                <table className="table table--compact">
                  <thead>
                    <tr>
                      <th>规则</th>
                      <th>状态</th>
                      <th>严重度</th>
                      <th>说明</th>
                      <th className="ta-right">证据</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.checks.map((check) => (
                      <tr key={check.id}>
                        <td>
                          <span className="field-name">{ruleLabel(check.rule_name)}</span>
                          <code className="field-key">{check.rule_name} · v{check.rule_version}</code>
                        </td>
                        <td>
                          <span
                            className={`badge badge--${
                              check.status === 'passed' ? 'green' : check.status === 'failed' ? 'red' : 'slate'
                            }`}
                          >
                            <span className="badge__dot" />
                            {checkStatusLabel(check.status)}
                          </span>
                        </td>
                        <td>
                          <span className={check.severity === 'high' ? 'text-danger' : 'muted'}>
                            {severityLabel(check.severity)}
                          </span>
                        </td>
                        <td className="cell-wrap">{check.message || '—'}</td>
                        <td className="ta-right num">{check.evidence_ids.length}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <aside className="detail-aside">
          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>决策</h3>
                <p className="panel__sub">最新决策与引用证据</p>
              </div>
            </div>
            {!detail.decision ? (
              <EmptyState compact title="暂无决策" description="运行工作流后生成决策结论。" />
            ) : (
              <div className="decision-card">
                <div className="decision-card__head">
                  <span className={`decision-pill decision-pill--${detail.decision.risk_level}`}>
                    {decisionLabel(detail.decision.decision)}
                  </span>
                  <RiskBadge risk={detail.decision.risk_level} compact />
                </div>
                <div className="decision-card__confidence">
                  <span>置信度</span>
                  <ConfidenceBar value={detail.decision.confidence} />
                </div>
                <p className="decision-card__reason">{detail.decision.reason}</p>
                <div className="decision-card__meta">
                  引用证据 {detail.decision.evidence_ids.length} 条 · {formatDateTime(detail.decision.created_at)}
                </div>
                <div className="chips chips--scroll">
                  {detail.decision.evidence_ids.map((evidenceId) => (
                    <span key={evidenceId} className="chip chip--mono">
                      {evidenceId}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>审批记录</h3>
                <p className="panel__sub">{detail.approvals.length} 次操作</p>
              </div>
            </div>
            {detail.approvals.length === 0 ? (
              <EmptyState compact title="暂无审批" description="进入人工审核后可在此查看记录。" />
            ) : (
              <ol className="approval-list">
                {detail.approvals.map((approval) => (
                  <li key={approval.id}>
                    <div className="approval-list__head">
                      <span className="badge badge--slate">{actionLabel(approval.action)}</span>
                      <span className="muted">{formatDateTime(approval.created_at)}</span>
                    </div>
                    <div className="approval-list__reviewer">审核人：{approval.reviewer_id}</div>
                    {approval.comment && <p className="approval-list__comment">{approval.comment}</p>}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>ERP 事务</h3>
                <p className="panel__sub">幂等写入结果</p>
              </div>
            </div>
            {!detail.erp_transaction ? (
              <EmptyState compact title="暂无 ERP 事务" description="审批通过或自动提交后生成。" />
            ) : (
              <div className="kv-stack">
                <div><span>ERP ID</span><code>{detail.erp_transaction.erp_id ?? '—'}</code></div>
                <div><span>状态</span><strong>{detail.erp_transaction.status}</strong></div>
                <div><span>幂等键</span><code>{truncateMiddle(detail.erp_transaction.idempotency_key, 10, 8)}</code></div>
                <div className="kv-stack__full">
                  <span>返回结果</span>
                  <pre className="json-block">{formatAny(detail.erp_transaction.result_json)}</pre>
                </div>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>上传材料</h3>
                <p className="panel__sub">支持图片 / PDF / 文本</p>
              </div>
            </div>
            <div className="upload-form">
              <label className="field">
                <span>选择文件</span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,.md,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tiff"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <label className="field">
                <span>声明类型</span>
                <select value={declaredType} onChange={(event) => setDeclaredType(event.target.value)}>
                  {DECLARED_TYPES.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="button button--primary button--block"
                onClick={() => void handleUpload()}
                disabled={uploading || !file}
              >
                {uploading ? '上传并解析中…' : '上传材料'}
              </button>
              {uploading && <Loading compact label="正在 OCR 与字段抽取…" />}
            </div>

            {uploadError && (
              <Notice tone="error" onClose={() => setUploadError('')}>
                {uploadError}
              </Notice>
            )}

            {uploadResult && (
              <div className="upload-result">
                <div className="upload-result__head">
                  <strong>{documentTypeLabel(uploadResult.document_type)}</strong>
                  <span className="badge badge--blue">{uploadResult.page_count} 页</span>
                </div>
                <div className="upload-result__meta">
                  OCR：{uploadResult.ocr_backend}
                  {uploadResult.ocr_degraded && (
                    <span className="badge badge--amber">降级</span>
                  )}
                </div>
                <div className="upload-result__fields">
                  {Object.entries(uploadResult.fields).map(([name, field]) => (
                    <span className="chip chip--mono" key={name}>
                      {name}={formatAny(field.value)}
                    </span>
                  ))}
                </div>
                {uploadResult.missing_fields.length > 0 && (
                  <div className="text-warn">缺失字段：{uploadResult.missing_fields.join('、')}</div>
                )}
                {uploadResult.errors.length > 0 && (
                  <ul className="error-list">
                    {uploadResult.errors.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                )}
              </div>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
