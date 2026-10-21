import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, errorMessage } from '../api';
import { EmptyState } from '../components/EmptyState';
import { Loading } from '../components/Loading';
import { Notice } from '../components/Notice';
import { RiskBadge } from '../components/RiskBadge';
import { StatusBadge } from '../components/StatusBadge';
import { TraceTimeline } from '../components/TraceTimeline';
import { formatDateTime, formatDuration } from '../format';
import { useRole } from '../role';
import type { ReplayResponse, TraceEvent } from '../types';

type TabKey = 'timeline' | 'raw';

/** Trace 页：确定性回放时间线 + 原始事件 payload */
export function TracePage() {
  const { id = '' } = useParams();
  const { role } = useRole();
  const [replay, setReplay] = useState<ReplayResponse | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<TabKey>('timeline');
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setReplay(null);
    setEvents([]);
    Promise.all([api.getReplay(id), api.getTrace(id)])
      .then(([replayData, traceData]) => {
        if (cancelled) return;
        setReplay(replayData);
        setEvents(traceData);
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
  }, [id, role, reloadKey]);

  const totalDuration = events.reduce((sum, event) => sum + (event.duration_ms ?? 0), 0);
  const traceId = events[0]?.trace_id ?? '—';

  return (
    <div className="page">
      <Link to={`/cases/${id}`} className="back-link">← 返回案件详情</Link>

      <div className="page-toolbar">
        <div className="page-toolbar__meta">
          <span className="eyebrow">可观测性</span>
          <h2>处理轨迹回放</h2>
          <p>
            案件 <code className="inline-code">{id}</code> · 基于已持久化 Trace 确定性重建，不触发 ERP 副作用
          </p>
        </div>
        <div className="page-toolbar__actions">
          <button
            type="button"
            className="button"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={loading}
          >
            重新加载
          </button>
        </div>
      </div>

      {error && (
        <Notice tone="error" title="轨迹加载失败" onClose={() => setError('')}>
          {error}
        </Notice>
      )}

      {loading ? (
        <div className="panel">
          <Loading label="正在重建处理轨迹…" />
        </div>
      ) : !replay ? (
        <div className="panel">
          <EmptyState title="暂无轨迹" description="运行工作流后生成 Trace 事件。" />
        </div>
      ) : (
        <>
          <section className="panel">
            <div className="trace-summary">
              <div className="trace-summary__main">
                <div className="case-inline-title">
                  <StatusBadge status={replay.final_status} />
                  <RiskBadge risk={replay.risk_level} />
                </div>
                <p>{replay.note}</p>
              </div>
              <div className="trace-summary__stats">
                <div><span>事件数</span><strong>{replay.event_count}</strong></div>
                <div><span>累计耗时</span><strong>{formatDuration(totalDuration)}</strong></div>
                <div><span>Trace ID</span><code>{traceId}</code></div>
              </div>
            </div>
          </section>

          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'timeline'}
              className={`tabs__btn${tab === 'timeline' ? ' tabs__btn--active' : ''}`}
              onClick={() => setTab('timeline')}
            >
              回放时间线
              <span className="tabs__count">{replay.timeline.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'raw'}
              className={`tabs__btn${tab === 'raw' ? ' tabs__btn--active' : ''}`}
              onClick={() => setTab('raw')}
            >
              原始事件
              <span className="tabs__count">{events.length}</span>
            </button>
          </div>

          {tab === 'timeline' ? (
            <section className="panel">
              <div className="panel__head">
                <div>
                  <h3>事件时间线</h3>
                  <p className="panel__sub">不同颜色代表不同事件类型，点击「展开详情」查看结构化载荷</p>
                </div>
              </div>
              <TraceTimeline items={replay.timeline} />
            </section>
          ) : (
            <section className="panel">
              <div className="panel__head">
                <div>
                  <h3>原始 Trace 事件</h3>
                  <p className="panel__sub">payload_json 折叠展示，含模型与工具元数据</p>
                </div>
              </div>
              {events.length === 0 ? (
                <EmptyState compact title="暂无原始事件" />
              ) : (
                <div className="raw-events">
                  {events.map((event) => (
                    <article className="raw-event" key={event.id}>
                      <div className="raw-event__head">
                        <span className="raw-event__type">{event.event_type}</span>
                        <span className="muted">{formatDateTime(event.created_at)}</span>
                        <span className="muted">actor: {event.actor ?? '—'}</span>
                        <span className="muted">{formatDuration(event.duration_ms)}</span>
                      </div>
                      <div className="raw-event__meta">
                        <span>trace: <code>{event.trace_id}</code></span>
                        <span>span: <code>{event.span_id ?? '—'}</code></span>
                        {event.parent_span_id && (
                          <span>parent: <code>{event.parent_span_id}</code></span>
                        )}
                        {event.tool_name && <span>tool: <code>{event.tool_name}</code></span>}
                        {event.model && <span>model: <code>{event.model}</code></span>}
                        {event.prompt_version && <span>prompt: <code>{event.prompt_version}</code></span>}
                        {event.error_code && <span className="text-danger">error: {event.error_code}</span>}
                      </div>
                      <details className="raw-event__payload">
                        <summary>payload_json</summary>
                        <pre className="json-block">{JSON.stringify(event.payload_json, null, 2)}</pre>
                      </details>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}
