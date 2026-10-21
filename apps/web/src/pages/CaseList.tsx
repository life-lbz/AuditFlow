import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorMessage } from '../api';
import { EmptyState } from '../components/EmptyState';
import { Loading } from '../components/Loading';
import { MetricCard } from '../components/MetricCard';
import { Notice } from '../components/Notice';
import { RiskBadge } from '../components/RiskBadge';
import { StatusBadge } from '../components/StatusBadge';
import {
  categoryLabel,
  formatCurrency,
  formatDateTime,
  statusLabel,
} from '../format';
import { useRole } from '../role';
import type { CaseOut, SystemInfo } from '../types';

/** 案件列表首页：系统概览 + 状态筛选 + 案件台账 */
export function CaseList() {
  const navigate = useNavigate();
  const { role, userId } = useRole();
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [cases, setCases] = useState<CaseOut[]>([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [infoError, setInfoError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setInfoError('');
    api
      .systemInfo()
      .then((data) => {
        if (!cancelled) setInfo(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setInfoError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setCases([]);
    api
      .listCases({ status: status || undefined, limit: 200 })
      .then((data) => {
        if (!cancelled) setCases(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [role, status, reloadKey]);

  const byStatus = info?.cases.by_status ?? {};
  const pendingCount = (byStatus.WAITING_APPROVAL ?? 0) + (byStatus.ESCALATED ?? 0);
  const needsInfoCount = byStatus.NEEDS_INFO ?? 0;
  const total = info?.cases.total ?? 0;
  const distribution = Object.entries(byStatus).sort((a, b) => b[1] - a[1]);

  return (
    <div className="page">
      <div className="page-toolbar">
        <div className="page-toolbar__meta">
          <span className="eyebrow">概览</span>
          <h2>审核运行态势</h2>
          <p>
            {role === 'employee'
              ? `员工视图：仅显示 ${userId} 本人提交的案件`
              : '审核视图：可查看全量案件与待审队列'}
          </p>
        </div>
        <div className="page-toolbar__actions">
          <button
            type="button"
            className="button"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={loading}
          >
            刷新数据
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={() => navigate('/cases/new')}
          >
            ＋ 新建案件
          </button>
        </div>
      </div>

      {infoError && (
        <Notice tone="warning" title="系统信息加载失败" onClose={() => setInfoError('')}>
          {infoError}
        </Notice>
      )}

      <div className="metric-grid metric-grid--4">
        <MetricCard
          label="案件总数"
          value={total}
          hint="全量登记案件"
          footer={<span className="muted">制度切片 {info?.policy_chunks ?? '—'} 条</span>}
        />
        <MetricCard
          label="待人工审核"
          value={pendingCount}
          hint={total > 0 ? `占全部案件 ${((pendingCount / total) * 100).toFixed(1)}%` : '等待工作流产出'}
          tone="warn"
          progress={total > 0 ? pendingCount / total : 0}
        />
        <MetricCard
          label="待补材料"
          value={needsInfoCount}
          hint="需员工重新提交凭证"
          tone={needsInfoCount > 0 ? 'danger' : 'good'}
          progress={total > 0 ? needsInfoCount / total : 0}
        />
        <MetricCard
          label="模型提供方"
          value={info?.model_provider ?? '—'}
          hint={`存储：${info?.storage_backend ?? '—'}`}
          tone="info"
          footer={<span className="muted">ERP：{info?.mock_erp_url ?? '—'}</span>}
        />
      </div>

      {distribution.length > 0 && (
        <div className="panel panel--compact">
          <div className="panel__head">
            <div>
              <h3>状态分布</h3>
              <p className="panel__sub">按案件生命周期聚合</p>
            </div>
          </div>
          <div className="status-strip">
            {distribution.map(([key, count]) => (
              <button
                key={key}
                type="button"
                className={`status-chip${status === key ? ' status-chip--active' : ''}`}
                onClick={() => setStatus(status === key ? '' : key)}
              >
                <StatusBadge status={key} showDot={false} />
                <span className="status-chip__count">{count}</span>
              </button>
            ))}
            {status && (
              <button type="button" className="link-button" onClick={() => setStatus('')}>
                清除筛选
              </button>
            )}
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel__head">
          <div>
            <h3>案件台账</h3>
            <p className="panel__sub">
              当前筛选：{status ? statusLabel(status) : '全部状态'} · 共 {cases.length} 条
            </p>
          </div>
          <div className="panel__tools">
            <label className="field field--inline">
              <span>状态</span>
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                <option value="">全部状态</option>
                {(info?.statuses ?? []).map((item) => (
                  <option key={item} value={item}>
                    {statusLabel(item)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {error && (
          <Notice tone="error" title="案件列表加载失败" onClose={() => setError('')}>
            {error}
          </Notice>
        )}

        {loading ? (
          <Loading label="正在加载案件台账…" />
        ) : cases.length === 0 ? (
          <EmptyState
            title={status ? '该状态下暂无案件' : '暂无案件'}
            description={
              status
                ? '尝试切换状态筛选，或新建一个案件后运行工作流。'
                : '创建第一个报销案件，上传材料并启动智能审核。'
            }
            action={
              <button type="button" className="button button--primary" onClick={() => navigate('/cases/new')}>
                新建案件
              </button>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="table table--hover">
              <thead>
                <tr>
                  <th>案件 ID</th>
                  <th>报销人</th>
                  <th>类型</th>
                  <th className="ta-right">金额</th>
                  <th>状态</th>
                  <th>风险</th>
                  <th>创建时间</th>
                  <th className="ta-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((item) => (
                  <tr
                    key={item.id}
                    tabIndex={0}
                    onClick={() => navigate(`/cases/${item.id}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') navigate(`/cases/${item.id}`);
                    }}
                  >
                    <td>
                      <code className="id-code">{item.id}</code>
                    </td>
                    <td>{item.employee_id}</td>
                    <td>
                      <span className="type-tag">{categoryLabel(item.category)}</span>
                    </td>
                    <td className="ta-right num">{formatCurrency(item.amount)}</td>
                    <td><StatusBadge status={item.status} /></td>
                    <td><RiskBadge risk={item.risk_level} compact /></td>
                    <td className="muted">{formatDateTime(item.created_at)}</td>
                    <td className="ta-right">
                      <button
                        type="button"
                        className="link-button"
                        onClick={(event) => {
                          event.stopPropagation();
                          navigate(`/cases/${item.id}`);
                        }}
                      >
                        查看 →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
