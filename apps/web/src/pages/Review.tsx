import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, api, errorMessage } from '../api';
import { EmptyState } from '../components/EmptyState';
import { Loading } from '../components/Loading';
import { Notice } from '../components/Notice';
import { RiskBadge } from '../components/RiskBadge';
import { StatusBadge } from '../components/StatusBadge';
import {
  actionLabel,
  categoryLabel,
  decisionLabel,
  formatAny,
  formatCurrency,
  formatDateTime,
  formatPercent,
  ruleLabel,
} from '../format';
import { useRole } from '../role';
import type { ApprovalResult, CaseDetail, CaseOut } from '../types';

const ACTIONS = [
  { value: 'approve', label: '批准并提交 ERP', tone: 'primary', hint: '通过审核并立即执行 ERP 付款' },
  { value: 'reject', label: '驳回', tone: 'danger', hint: '终结案件并记录驳回原因' },
  { value: 'request_info', label: '要求补材料', tone: 'warn', hint: '案件进入 NEEDS_INFO，等待员工补充' },
  { value: 'modify_fields', label: '修改字段后重跑', tone: 'info', hint: '仅记录意见，需修改后调用重试' },
  { value: 'terminate', label: '终止', tone: 'danger', hint: '直接终结案件，不再流转' },
];

/** 审核中心：待审队列 + 案件摘要 + 人工审批动作 */
export function Review() {
  const { role, setRole } = useRole();
  const [pending, setPending] = useState<CaseOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [forbidden, setForbidden] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');

  const [action, setAction] = useState('approve');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState('');
  const [result, setResult] = useState<ApprovalResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setForbidden(false);
    api
      .pendingApprovals()
      .then((data) => {
        if (cancelled) return;
        setPending(data);
        setSelectedId((current) =>
          data.some((item) => item.id === current) ? current : (data[0]?.id ?? ''),
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPending([]);
        setSelectedId('');
        setError(errorMessage(err));
        setForbidden(err instanceof ApiError && err.status === 403);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [role, reloadKey]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError('');
    setActionError('');
    api
      .getCase(selectedId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setDetailError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, role]);

  async function handleSubmit() {
    if (!selectedId) return;
    if (action !== 'approve' && !comment.trim()) {
      setActionError('该操作需要填写审批意见');
      return;
    }
    setSubmitting(true);
    setActionError('');
    setResult(null);
    try {
      const response = await api.submitApproval(selectedId, action, comment.trim());
      setResult(response);
      const remaining = pending.filter((item) => item.id !== selectedId);
      setPending(remaining);
      setSelectedId(remaining[0]?.id ?? '');
      setDetail(null);
      setComment('');
      setAction('approve');
    } catch (err: unknown) {
      setActionError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  const failedChecks = detail?.checks.filter((check) => check.status === 'failed') ?? [];

  return (
    <div className="page">
      <div className="page-toolbar">
        <div className="page-toolbar__meta">
          <span className="eyebrow">人工复核</span>
          <h2>待审核队列</h2>
          <p>选择案件查看风险与决策依据，提交审批后自动从队列移除。</p>
        </div>
        <div className="page-toolbar__actions">
          <span className="badge badge--amber">待审 {pending.length}</span>
          <button
            type="button"
            className="button"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={loading}
          >
            刷新队列
          </button>
        </div>
      </div>

      {error && (
        <Notice tone={forbidden ? 'warning' : 'error'} title={forbidden ? '当前角色无审核权限' : '待审队列加载失败'}>
          <p>{error}</p>
          {forbidden && (
            <div className="notice__actions">
              <button type="button" className="button button--primary button--sm" onClick={() => setRole('reviewer')}>
                切换为审核员
              </button>
              <Link to="/cases" className="button button--sm">返回案件列表</Link>
            </div>
          )}
        </Notice>
      )}

      {result && (
        <Notice tone="success" title="审批提交成功" onClose={() => setResult(null)}>
          <p>{result.detail || `${actionLabel(result.action)} · 当前状态 ${result.status}`}</p>
          {result.erp && (
            <pre className="json-block">{formatAny(result.erp)}</pre>
          )}
        </Notice>
      )}

      {loading ? (
        <div className="panel">
          <Loading label="正在加载待审案件…" />
        </div>
      ) : pending.length === 0 && !error ? (
        <div className="panel">
          <EmptyState
            title="审核队列为空"
            description="当前没有处于待审核或已升级状态的案件。"
            action={<Link to="/cases" className="button">查看全部案件</Link>}
          />
        </div>
      ) : pending.length > 0 ? (
        <div className="review-layout">
          <aside className="panel review-queue">
            <div className="panel__head">
              <div>
                <h3>待审案件</h3>
                <p className="panel__sub">按创建时间排序</p>
              </div>
            </div>
            <div className="review-queue__list">
              {pending.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`queue-item${item.id === selectedId ? ' queue-item--active' : ''}`}
                  onClick={() => {
                    setResult(null);
                    setSelectedId(item.id);
                  }}
                >
                  <div className="queue-item__top">
                    <code className="id-code">{item.id}</code>
                    <RiskBadge risk={item.risk_level} compact />
                  </div>
                  <div className="queue-item__meta">
                    <span>{item.employee_id}</span>
                    <span>{categoryLabel(item.category)}</span>
                    <strong className="num">{formatCurrency(item.amount)}</strong>
                  </div>
                  <div className="queue-item__bottom">
                    <StatusBadge status={item.status} showDot={false} />
                    <span className="muted">{formatDateTime(item.created_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          </aside>

          <div className="review-main">
            {detailLoading ? (
              <div className="panel"><Loading label="正在加载案件摘要…" /></div>
            ) : detailError ? (
              <div className="panel">
                <Notice tone="error" title="案件详情加载失败">{detailError}</Notice>
              </div>
            ) : !detail ? (
              <div className="panel">
                <EmptyState title="请选择案件" description="从左侧队列选择一条待审案件查看摘要。" />
              </div>
            ) : (
              <>
                <section className="panel">
                  <div className="panel__head">
                    <div>
                      <div className="case-inline-title">
                        <code className="id-code id-code--lg">{detail.id}</code>
                        <StatusBadge status={detail.status} />
                        <RiskBadge risk={detail.risk_level} />
                      </div>
                      <p className="panel__sub">
                        {detail.employee_id} · {categoryLabel(detail.category)} · {formatCurrency(detail.amount)}
                      </p>
                    </div>
                    <Link to={`/cases/${detail.id}`} className="button button--sm">查看完整详情 →</Link>
                  </div>

                  <p className="review-desc">{detail.description || '（未填写报销事由）'}</p>

                  <div className="review-facts">
                    <div><span>创建时间</span><strong>{formatDateTime(detail.created_at)}</strong></div>
                    <div><span>状态版本</span><strong>v{detail.state_version}</strong></div>
                    <div><span>材料</span><strong>{detail.documents.length} 份</strong></div>
                    <div><span>证据</span><strong>{detail.evidence.length} 条</strong></div>
                    <div><span>制度条款</span><strong>{detail.policy_evidence.length} 条</strong></div>
                    <div><span>历史审批</span><strong>{detail.approvals.length} 次</strong></div>
                  </div>

                  {detail.missing_documents.length > 0 && (
                    <Notice tone="warning" title="缺少材料">
                      {detail.missing_documents.join('、')}
                    </Notice>
                  )}
                </section>

                <div className="review-summary-grid">
                  <section className="panel">
                    <div className="panel__head">
                      <div>
                        <h3>决策结论</h3>
                        <p className="panel__sub">系统最新决策</p>
                      </div>
                    </div>
                    {detail.decision ? (
                      <div className="decision-card">
                        <div className="decision-card__head">
                          <span className={`decision-pill decision-pill--${detail.decision.risk_level}`}>
                            {decisionLabel(detail.decision.decision)}
                          </span>
                          <span className="muted">置信度 {formatPercent(detail.decision.confidence)}</span>
                        </div>
                        <p className="decision-card__reason">{detail.decision.reason}</p>
                        <div className="decision-card__meta">
                          引用证据 {detail.decision.evidence_ids.length} 条
                        </div>
                      </div>
                    ) : (
                      <EmptyState compact title="暂无决策" description="案件可能仍在流转中。" />
                    )}
                  </section>

                  <section className="panel">
                    <div className="panel__head">
                      <div>
                        <h3>规则失败</h3>
                        <p className="panel__sub">{failedChecks.length} 条未通过</p>
                      </div>
                    </div>
                    {failedChecks.length === 0 ? (
                      <div className="ok-state">所有规则均通过或跳过</div>
                    ) : (
                      <ul className="failed-list">
                        {failedChecks.map((check) => (
                          <li key={check.id}>
                            <div className="failed-list__head">
                              <strong>{ruleLabel(check.rule_name)}</strong>
                              <span className={`badge badge--${check.severity === 'high' ? 'red' : 'amber'}`}>
                                {check.severity}
                              </span>
                            </div>
                            <p>{check.message}</p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>

                  <section className="panel">
                    <div className="panel__head">
                      <div>
                        <h3>关键证据</h3>
                        <p className="panel__sub">字段级引用</p>
                      </div>
                    </div>
                    {detail.evidence.length === 0 ? (
                      <EmptyState compact title="暂无证据" />
                    ) : (
                      <ul className="mini-evidence">
                        {detail.evidence.slice(0, 6).map((item) => (
                          <li key={item.id}>
                            <span>{item.quote}</span>
                            <span className="score">{formatPercent(item.score)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                </div>

                <section className="panel">
                  <div className="panel__head">
                    <div>
                      <h3>制度依据</h3>
                      <p className="panel__sub">Top {Math.min(4, detail.policy_evidence.length)} 命中条款</p>
                    </div>
                  </div>
                  {detail.policy_evidence.length === 0 ? (
                    <EmptyState compact title="暂无制度证据" />
                  ) : (
                    <div className="policy-list policy-list--compact">
                      {detail.policy_evidence.slice(0, 4).map((item) => (
                        <article className="policy-item" key={item.id}>
                          <div className="policy-item__head">
                            <code>{item.citation}</code>
                            <span className="score">score {item.score.toFixed(3)}</span>
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
                      <h3>审批操作</h3>
                      <p className="panel__sub">选择动作并填写意见后提交</p>
                    </div>
                    <span className="muted">审核人：{role}</span>
                  </div>

                  {actionError && (
                    <Notice tone="error" title="提交失败" onClose={() => setActionError('')}>
                      {actionError}
                    </Notice>
                  )}

                  <div className="action-options">
                    {ACTIONS.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        className={`action-option action-option--${item.tone}${
                          action === item.value ? ' action-option--active' : ''
                        }`}
                        onClick={() => setAction(item.value)}
                      >
                        <span className="action-option__label">{item.label}</span>
                        <span className="action-option__hint">{item.hint}</span>
                      </button>
                    ))}
                  </div>

                  <label className="field">
                    <span>审批意见{action !== 'approve' ? ' *' : ''}</span>
                    <textarea
                      rows={3}
                      value={comment}
                      onChange={(event) => setComment(event.target.value)}
                      placeholder={
                        action === 'approve'
                          ? '可选：补充审批说明'
                          : '请说明原因，意见将写入审批记录'
                      }
                    />
                  </label>

                  <div className="form__actions">
                    <button
                      type="button"
                      className={`button ${action === 'reject' || action === 'terminate' ? 'button--danger' : 'button--primary'}`}
                      onClick={() => void handleSubmit()}
                      disabled={submitting}
                    >
                      {submitting ? '提交中…' : `提交：${actionLabel(action)}`}
                    </button>
                  </div>
                  {action === 'modify_fields' && (
                    <p className="form-hint">
                      该操作仅记录修改意见，不会改变案件状态；请前往案件详情修改材料后调用「重试 / 继续」。
                    </p>
                  )}
                  {submitting && <Loading compact label="正在提交审批…" />}
                </section>
              </>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
