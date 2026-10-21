import { useEffect, useState } from 'react';
import { api, errorMessage } from '../api';
import { EmptyState } from '../components/EmptyState';
import { Loading } from '../components/Loading';
import { MetricCard } from '../components/MetricCard';
import { Notice } from '../components/Notice';
import { formatAny, formatDateTime, formatDuration, formatPercent } from '../format';
import type { EvalRunResponse, EvalRunSummary } from '../types';

const MODES = [
  { value: 'single_agent_verifier', label: '单 Agent + Verifier' },
  { value: 'fixed_workflow', label: '固定工作流' },
  { value: 'single_agent', label: '单 Agent' },
  { value: 'single_agent_verifier_researcher', label: '单 Agent + Verifier + 检索' },
];

const MODE_LABELS: Record<string, string> = {
  fixed_workflow: '固定工作流',
  single_agent: '单 Agent',
  single_agent_verifier: '单 Agent + Verifier',
  single_agent_verifier_researcher: '单 Agent + Verifier + 检索',
};

function RiskCell({ value }: { value: string }) {
  const tone = value === 'high' ? 'red' : value === 'medium' ? 'amber' : 'green';
  return <span className={`badge badge--${tone} badge--compact`}>{value}</span>;
}

/** 评测面板：配置对照实验、展示质量指标、失败归因与历史记录 */
export function EvalPanel() {
  const [mode, setMode] = useState('single_agent_verifier');
  const [limit, setLimit] = useState(30);
  const [autoSubmit, setAutoSubmit] = useState(false);

  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState('');
  const [result, setResult] = useState<EvalRunResponse | null>(null);

  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState('');

  async function fetchRuns(showLoading = true) {
    if (showLoading) setRunsLoading(true);
    setRunsError('');
    try {
      const data = await api.listEvalRuns();
      setRuns(data.runs);
    } catch (err: unknown) {
      setRunsError(errorMessage(err));
    } finally {
      if (showLoading) setRunsLoading(false);
    }
  }

  useEffect(() => {
    void fetchRuns(true);
  }, []);

  async function handleRun() {
    setRunning(true);
    setRunError('');
    try {
      const data = await api.runEval({
        suite: 'auditflow-v1',
        mode,
        limit,
        auto_submit: autoSubmit,
      });
      setResult(data);
      await fetchRuns(false);
    } catch (err: unknown) {
      setRunError(errorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  const metrics = result?.metrics;

  return (
    <div className="page">
      <div className="page-toolbar">
        <div className="page-toolbar__meta">
          <span className="eyebrow">质量评估</span>
          <h2>评测与对照实验</h2>
          <p>在同一数据集上比较不同编排模式，关注准确率、错误自动通过率与人工接管成本。</p>
        </div>
      </div>

      <section className="panel">
        <div className="panel__head">
          <div>
            <h3>运行配置</h3>
            <p className="panel__sub">同步执行，最多 120 条；数据量大时请使用后端 CLI 全量评测</p>
          </div>
          <span className="badge badge--slate">suite · auditflow-v1</span>
        </div>

        <div className="eval-form">
          <label className="field">
            <span>编排模式</span>
            <select value={mode} onChange={(event) => setMode(event.target.value)}>
              {MODES.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
            <small>Agent 模式适用于复杂/异常案件，固定工作流作为对照基线</small>
          </label>

          <label className="field">
            <span>评测条数 · <strong className="num">{limit}</strong></span>
            <input
              className="range"
              type="range"
              min={1}
              max={120}
              step={1}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
            />
            <small>1–120 条，按案件 ID 顺序抽样</small>
          </label>

          <label className="checkbox checkbox--tile">
            <input
              type="checkbox"
              checked={autoSubmit}
              onChange={(event) => setAutoSubmit(event.target.checked)}
            />
            <span>
              允许自动提交
              <small>开启后会统计错误自动通过率与 ERP 副作用</small>
            </span>
          </label>

          <div className="eval-form__action">
            <button
              type="button"
              className="button button--primary button--block"
              onClick={() => void handleRun()}
              disabled={running}
            >
              {running ? '评测执行中…' : '开始评测'}
            </button>
            {running && <Loading compact label="正在逐案运行工作流，请稍候…" />}
          </div>
        </div>

        {runError && (
          <Notice tone="error" title="评测执行失败" onClose={() => setRunError('')}>
            {runError}
          </Notice>
        )}
        {running && (
          <div className="eval-running">
            正在执行 {limit} 条案件评测，预计需要数十秒，请勿关闭页面。
          </div>
        )}
      </section>

      {!result ? (
        <div className="panel">
          <EmptyState
            title="尚无本次评测结果"
            description="选择模式与条数后点击「开始评测」，结果将在此展示指标、失败案例与分组统计。"
          />
        </div>
      ) : (
        <>
          <section className="panel">
            <div className="panel__head">
              <div>
                <h3>核心指标</h3>
                <p className="panel__sub">
                  {result.suite} · {MODE_LABELS[result.mode] ?? result.mode} · {metrics?.total ?? 0} 条 ·
                  耗时 {result.duration_secs.toFixed(1)} s
                </p>
              </div>
              <span className="badge badge--blue">auto_submit {autoSubmit ? 'on' : 'off'}</span>
            </div>

            {metrics && (
              <>
                <div className="metric-grid metric-grid--3">
                  <MetricCard
                    label="决策准确率"
                    value={formatPercent(metrics.decision_accuracy)}
                    progress={metrics.decision_accuracy}
                    tone="good"
                    ring
                    hint="决策结论与标注一致的比例"
                  />
                  <MetricCard
                    label="风险准确率"
                    value={formatPercent(metrics.risk_accuracy)}
                    progress={metrics.risk_accuracy}
                    tone="good"
                    ring
                    hint="风险等级判定准确率"
                  />
                  <MetricCard
                    label="错误自动通过率"
                    value={formatPercent(metrics.error_auto_approve_rate)}
                    progress={metrics.error_auto_approve_rate}
                    tone={metrics.error_auto_approve_rate > 0 ? 'danger' : 'good'}
                    ring
                    hint="越低越好，错误放行会造成资金风险"
                  />
                  <MetricCard
                    label="任务成功率"
                    value={formatPercent(metrics.task_success_rate)}
                    progress={metrics.task_success_rate}
                    tone="good"
                    ring
                    hint="端到端完成处理的任务占比"
                  />
                  <MetricCard
                    label="人工接管率"
                    value={formatPercent(metrics.human_takeover_rate)}
                    progress={metrics.human_takeover_rate}
                    tone="warn"
                    ring
                    hint="进入人工审核的案件占比"
                  />
                  <MetricCard
                    label="字段抽取 F1"
                    value={formatPercent(metrics.field_f1)}
                    progress={metrics.field_f1}
                    tone="info"
                    ring
                    hint={`P ${formatPercent(metrics.field_precision)} / R ${formatPercent(metrics.field_recall)}`}
                  />
                  <MetricCard
                    label="制度 Recall@5"
                    value={formatPercent(metrics.policy_recall_at_5)}
                    progress={metrics.policy_recall_at_5}
                    tone="info"
                    ring
                    hint="Top-5 检索命中率"
                  />
                  <MetricCard
                    label="证据覆盖率"
                    value={formatPercent(metrics.evidence_coverage)}
                    progress={metrics.evidence_coverage}
                    tone="good"
                    ring
                    hint={`证据精确率 ${formatPercent(metrics.evidence_precision)}`}
                  />
                  <MetricCard
                    label="P95 延迟"
                    value={`${metrics.p95_latency_ms.toFixed(0)} ms`}
                    tone="default"
                    hint={`平均延迟 ${metrics.avg_latency_ms.toFixed(0)} ms`}
                  />
                </div>

                <div className="stat-strip">
                  <div><span>Agent 使用率</span><strong>{formatPercent(metrics.agent_usage_rate)}</strong></div>
                  <div><span>平均工具步数</span><strong>{metrics.avg_tool_steps.toFixed(2)}</strong></div>
                  <div><span>平均延迟</span><strong>{metrics.avg_latency_ms.toFixed(0)} ms</strong></div>
                  <div><span>字段精确率</span><strong>{formatPercent(metrics.field_precision)}</strong></div>
                  <div><span>字段召回率</span><strong>{formatPercent(metrics.field_recall)}</strong></div>
                  <div><span>评测耗时</span><strong>{formatDuration(result.duration_secs * 1000)}</strong></div>
                </div>
              </>
            )}

            {result.errors.length > 0 && (
              <Notice tone="warning" title="执行过程中的警告">
                <ul className="error-list">
                  {result.errors.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </Notice>
            )}
          </section>

          {metrics && (
            <div className="split-layout split-layout--even">
              <section className="panel">
                <div className="panel__head">
                  <div>
                    <h3>失败案例</h3>
                    <p className="panel__sub">按决策准确性归因</p>
                  </div>
                  <span className="badge badge--red">{metrics.failures.length}</span>
                </div>
                {metrics.failures.length === 0 ? (
                  <div className="ok-state">本次评测无失败案例</div>
                ) : (
                  <div className="table-wrap">
                    <table className="table table--compact">
                      <thead>
                        <tr>
                          <th>案件</th>
                          <th>期望 / 实际</th>
                          <th>风险</th>
                          <th>归因</th>
                        </tr>
                      </thead>
                      <tbody>
                        {metrics.failures.map((failure) => (
                          <tr key={failure.case_id}>
                            <td>
                              <code className="id-code">{failure.case_id}</code>
                              <div className="cell-sub">{(failure.tags ?? []).join(' · ')}</div>
                            </td>
                            <td>
                              <div>{failure.expected} → <strong className="text-danger">{failure.actual}</strong></div>
                              <div className="cell-sub">{failure.verifier || '无 Verifier 结论'}</div>
                            </td>
                            <td>
                              <div className="risk-pair">
                                <RiskCell value={failure.expected_risk} />
                                <span>→</span>
                                <RiskCell value={failure.actual_risk} />
                              </div>
                            </td>
                            <td className="cell-wrap">{failure.attribution}</td>
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
                    <h3>按标签分组</h3>
                    <p className="panel__sub">定位薄弱场景</p>
                  </div>
                </div>
                {Object.keys(metrics.by_tag).length === 0 ? (
                  <EmptyState compact title="暂无分组数据" />
                ) : (
                  <div className="table-wrap">
                    <table className="table table--compact">
                      <thead>
                        <tr>
                          <th>标签</th>
                          <th className="ta-right">样本</th>
                          <th>决策准确率</th>
                          <th className="ta-right">平均步数</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(metrics.by_tag).map(([tag, item]) => (
                          <tr key={tag}>
                            <td><span className="chip">{tag}</span></td>
                            <td className="ta-right num">{item.count}</td>
                            <td>
                              <div className="inline-progress">
                                <div className="progress progress--thin">
                                  <span
                                    className="progress__bar"
                                    style={{ width: `${Math.min(100, item.decision_accuracy * 100)}%` }}
                                  />
                                </div>
                                <span className="num">{formatPercent(item.decision_accuracy)}</span>
                              </div>
                            </td>
                            <td className="ta-right num">{item.avg_tool_steps.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </div>
          )}

          {metrics && metrics.field_errors.length > 0 && (
            <section className="panel">
              <div className="panel__head">
                <div>
                  <h3>字段错误样本</h3>
                  <p className="panel__sub">抽取值与标注值不一致，最多展示 10 条</p>
                </div>
              </div>
              <div className="table-wrap">
                <table className="table table--compact">
                  <thead>
                    <tr>
                      <th>案件</th>
                      <th>字段</th>
                      <th>期望值</th>
                      <th>实际值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.field_errors.map((error, index) => (
                      <tr key={`${error.case_id}-${error.field}-${index}`}>
                        <td><code className="id-code">{error.case_id}</code></td>
                        <td><code>{error.field}</code></td>
                        <td>{formatAny(error.expected)}</td>
                        <td className="text-danger">{formatAny(error.actual)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}

      <section className="panel">
        <div className="panel__head">
          <div>
            <h3>历史评测</h3>
            <p className="panel__sub">最近 20 次运行记录</p>
          </div>
          <button type="button" className="button button--sm" onClick={() => void fetchRuns(true)} disabled={runsLoading}>
            刷新
          </button>
        </div>

        {runsError && (
          <Notice tone="error" title="历史记录加载失败" onClose={() => setRunsError('')}>
            {runsError}
          </Notice>
        )}

        {runsLoading ? (
          <Loading label="正在加载历史评测…" compact />
        ) : runs.length === 0 ? (
          <EmptyState compact title="暂无历史评测" description="完成一次评测后自动记录。" />
        ) : (
          <div className="table-wrap">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>运行 ID</th>
                  <th>套件</th>
                  <th>模式</th>
                  <th className="ta-right">样本</th>
                  <th>决策准确率</th>
                  <th>字段 F1</th>
                  <th className="ta-right">P95</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td><code className="id-code">{run.id}</code></td>
                    <td>{run.suite}</td>
                    <td>{MODE_LABELS[run.agent_mode] ?? run.agent_mode}</td>
                    <td className="ta-right num">{run.metrics.total ?? '—'}</td>
                    <td>
                      <span className="num">{formatPercent(run.metrics.decision_accuracy ?? 0)}</span>
                    </td>
                    <td>
                      <span className="num">{formatPercent(run.metrics.field_f1 ?? 0)}</span>
                    </td>
                    <td className="ta-right num">
                      {run.metrics.p95_latency_ms !== undefined
                        ? `${run.metrics.p95_latency_ms.toFixed(0)} ms`
                        : '—'}
                    </td>
                    <td className="muted">{formatDateTime(run.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
