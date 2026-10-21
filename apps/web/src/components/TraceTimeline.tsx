import { useEffect, useState, type ReactNode } from 'react';
import {
  formatAny,
  formatClock,
  formatDuration,
  formatPercent,
  decisionLabel,
} from '../format';
import type { ReplayTimelineItem } from '../types';

interface TraceTimelineProps {
  items: ReplayTimelineItem[];
}

interface EventMeta {
  icon: string;
  tone: string;
  label: string;
}

const EVENT_META: Record<string, EventMeta> = {
  case_created: { icon: '＋', tone: 'slate', label: '案件创建' },
  document_uploaded: { icon: '▤', tone: 'teal', label: '文档上传' },
  document_extracted: { icon: '⌕', tone: 'teal', label: '字段抽取' },
  evidence_created: { icon: '❖', tone: 'cyan', label: '证据生成' },
  policy_retrieved: { icon: '§', tone: 'purple', label: '制度检索' },
  state_transition: { icon: '⇄', tone: 'blue', label: '状态流转' },
  rule_evaluated: { icon: '⚖', tone: 'amber', label: '规则校验' },
  decision_made: { icon: '◆', tone: 'indigo', label: '决策生成' },
  verifier_finished: { icon: '✓', tone: 'cyan', label: 'Verifier 复核' },
  agent_started: { icon: '▷', tone: 'purple', label: 'Agent 启动' },
  agent_finished: { icon: '◁', tone: 'purple', label: 'Agent 结束' },
  tool_completed: { icon: '⚙', tone: 'slate', label: '工具调用' },
  approval_received: { icon: '✎', tone: 'rose', label: '人工审批' },
  erp_executed: { icon: '↗', tone: 'green', label: 'ERP 执行' },
  error: { icon: '!', tone: 'red', label: '异常' },
};

function metaOf(type: string): EventMeta {
  return EVENT_META[type] ?? { icon: '·', tone: 'slate', label: type };
}

function KeyValue({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="kv-grid">
      {Object.entries(data).map(([key, value]) => (
        <div className="kv-row" key={key}>
          <span className="kv-key">{key}</span>
          <span className="kv-value">
            {value !== null && typeof value === 'object' ? (
              <pre className="json-inline">{JSON.stringify(value, null, 2)}</pre>
            ) : (
              formatAny(value)
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="trace-detail__section">
      <div className="trace-detail__title">{title}</div>
      {children}
    </div>
  );
}

/** 回放时间线：按 seq 展示事件，支持展开 transition / fields / citations / decision */
export function TraceTimeline({ items }: TraceTimelineProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  useEffect(() => {
    setExpanded(new Set());
  }, [items]);

  function toggle(seq: number) {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });
  }

  if (items.length === 0) {
    return <div className="muted">暂无回放事件</div>;
  }

  return (
    <ol className="timeline">
      {items.map((item) => {
        const meta = metaOf(item.event_type);
        const hasDetail = Boolean(item.transition || item.fields || item.citations || item.decision);
        const isOpen = expanded.has(item.seq);

        return (
          <li className={`timeline__item timeline--${meta.tone}`} key={`${item.seq}-${item.event_type}`}>
            <div className="timeline__time">
              <span className="timeline__clock">{formatClock(item.at)}</span>
              <span className="timeline__seq">#{item.seq}</span>
            </div>
            <div className="timeline__rail">
              <span className="timeline__icon" aria-hidden="true">{meta.icon}</span>
            </div>
            <div className="timeline__content">
              <div className="timeline__head">
                <span className="timeline__type">{meta.label}</span>
                <code className="timeline__raw">{item.event_type}</code>
                {item.duration_ms !== null && (
                  <span className="timeline__duration">{formatDuration(item.duration_ms)}</span>
                )}
              </div>
              <div className="timeline__summary">{item.summary}</div>
              <div className="timeline__meta">
                <span>操作者：{item.actor ?? 'system'}</span>
                {item.decision?.decision !== undefined && (
                  <span>决策：{decisionLabel(String(item.decision.decision))}</span>
                )}
                {item.decision?.confidence !== undefined && (
                  <span>置信度：{formatPercent(Number(item.decision.confidence))}</span>
                )}
              </div>

              {hasDetail && (
                <div className="trace-detail">
                  <button type="button" className="link-button" onClick={() => toggle(item.seq)}>
                    {isOpen ? '收起详情 ▴' : '展开详情 ▾'}
                  </button>
                  {isOpen && (
                    <div className="trace-detail__body">
                      {item.transition && (
                        <DetailSection title="状态流转">
                          <KeyValue data={item.transition} />
                        </DetailSection>
                      )}
                      {item.fields && (
                        <DetailSection title={`字段抽取（${Object.keys(item.fields).length}）`}>
                          <table className="table table--compact">
                            <thead>
                              <tr>
                                <th>字段</th>
                                <th>值</th>
                                <th>置信度</th>
                              </tr>
                            </thead>
                            <tbody>
                              {Object.entries(item.fields).map(([name, field]) => (
                                <tr key={name}>
                                  <td><code>{name}</code></td>
                                  <td>{formatAny(field.value)}</td>
                                  <td>
                                    <span className={`score${field.confidence < 0.7 ? ' score--warn' : ''}`}>
                                      {formatPercent(field.confidence)}
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </DetailSection>
                      )}
                      {item.citations !== undefined && item.citations !== null && (
                        <DetailSection title="制度引用">
                          {Array.isArray(item.citations) ? (
                            <div className="chips">
                              {item.citations.map((citation, index) => (
                                <span className="chip" key={index}>
                                  {typeof citation === 'string' ? citation : JSON.stringify(citation)}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <pre className="json-block">{JSON.stringify(item.citations, null, 2)}</pre>
                          )}
                        </DetailSection>
                      )}
                      {item.decision && (
                        <DetailSection title="决策内容">
                          <KeyValue data={item.decision} />
                        </DetailSection>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
