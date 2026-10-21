import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { apiUrl } from '../api';
import { documentTypeLabel, formatPercent, formatAny } from '../format';
import type { CaseDocument, EvidenceItem } from '../types';
import { EmptyState } from './EmptyState';

interface EvidencePanelProps {
  documents: CaseDocument[];
  evidence: EvidenceItem[];
  activeEvidenceId: string | null;
  onSelectEvidence: (id: string) => void;
}

/** 在 OCR 文本中定位并高亮证据引用 */
function highlightQuote(text: string, quote: string | null): ReactNode {
  if (!quote) return text;
  const candidates = [quote.trim(), quote.replace(/^[^:：]*[:：]\s*/, '').trim()].filter(
    (item) => item.length >= 2,
  );
  for (const candidate of candidates) {
    const index = text.indexOf(candidate);
    if (index >= 0) {
      return (
        <>
          {text.slice(0, index)}
          <mark className="quote-mark">{candidate}</mark>
          {text.slice(index + candidate.length)}
        </>
      );
    }
  }
  return text;
}

function pickPage(doc: CaseDocument | undefined, pageNo: number | null): number {
  if (!doc || doc.pages.length === 0) return 1;
  if (pageNo && doc.pages.some((page) => page.page_no === pageNo)) return pageNo;
  return doc.pages[0].page_no;
}

/** 证据查看器：左侧文档页面/OCR，右侧证据条目，点击证据联动高亮 */
export function EvidencePanel({
  documents,
  evidence,
  activeEvidenceId,
  onSelectEvidence,
}: EvidencePanelProps) {
  const [docId, setDocId] = useState<string>(documents[0]?.id ?? '');
  const [pageNo, setPageNo] = useState<number>(documents[0]?.pages[0]?.page_no ?? 1);

  useEffect(() => {
    if (documents.length === 0) {
      setDocId('');
      return;
    }
    if (!documents.some((doc) => doc.id === docId)) {
      setDocId(documents[0].id);
      setPageNo(documents[0].pages[0]?.page_no ?? 1);
    }
  }, [documents, docId]);

  const document = useMemo(
    () => documents.find((doc) => doc.id === docId) ?? documents[0],
    [documents, docId],
  );

  useEffect(() => {
    if (!document) return;
    if (!document.pages.some((page) => page.page_no === pageNo)) {
      setPageNo(document.pages[0]?.page_no ?? 1);
    }
  }, [document, pageNo]);

  const page = document?.pages.find((item) => item.page_no === pageNo) ?? document?.pages[0];
  const activeEvidence = evidence.find((item) => item.id === activeEvidenceId) ?? null;

  function handleSelect(item: EvidenceItem) {
    const targetDoc = documents.find((doc) => doc.id === item.source_id);
    if (targetDoc) {
      setDocId(targetDoc.id);
      setPageNo(pickPage(targetDoc, item.page_no));
    }
    onSelectEvidence(item.id);
  }

  if (documents.length === 0) {
    return (
      <EmptyState
        compact
        title="暂无文档"
        description="上传发票、流水或审批邮件后，可在此查看页面与证据摘录。"
      />
    );
  }

  return (
    <div className="evidence-panel">
      <div className="evidence-viewer">
        <div className="evidence-toolbar">
          <div className="doc-tabs" role="tablist" aria-label="文档选择">
            {documents.map((doc) => (
              <button
                key={doc.id}
                type="button"
                role="tab"
                aria-selected={doc.id === document?.id}
                className={`doc-tab${doc.id === document?.id ? ' doc-tab--active' : ''}`}
                onClick={() => {
                  setDocId(doc.id);
                  setPageNo(doc.pages[0]?.page_no ?? 1);
                }}
              >
                <span>{doc.type_label || documentTypeLabel(doc.type)}</span>
                <span className="doc-tab__count">{doc.page_count} 页</span>
              </button>
            ))}
          </div>
          {document && document.pages.length > 1 && (
            <label className="field field--inline field--compact">
              <span>页码</span>
              <select
                value={pageNo}
                onChange={(event) => setPageNo(Number(event.target.value))}
              >
                {document.pages.map((item) => (
                  <option key={item.page_no} value={item.page_no}>
                    第 {item.page_no} 页
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        {activeEvidence ? (
          <div className="evidence-active">
            <span className="evidence-active__label">当前证据 · {formatPercent(activeEvidence.score)}</span>
            <blockquote>{activeEvidence.quote}</blockquote>
          </div>
        ) : (
          <div className="evidence-hint">点击右侧证据条目，可在页面文本中定位并高亮引用。</div>
        )}

        <div className="evidence-canvas">
          {page?.image_url ? (
            <div className="evidence-image">
              <img src={apiUrl(page.image_url)} alt={`${document?.type_label ?? '文档'}第 ${page.page_no} 页`} />
            </div>
          ) : (
            <div className="evidence-noimage">该页无原始图片，以下为 OCR 文本</div>
          )}
          <div className="ocr-block">
            <div className="ocr-block__head">
              <span>OCR 文本</span>
              <span className="muted">
                {document?.type_label} · 第 {page?.page_no ?? 1} 页
              </span>
            </div>
            <pre className="ocr-text">
              {page ? highlightQuote(page.ocr_text, activeEvidence?.quote ?? null) : '暂无页面内容'}
            </pre>
          </div>
        </div>
      </div>

      <div className="evidence-list">
        <div className="evidence-list__head">
          <span>证据条目</span>
          <span className="muted">{evidence.length} 条</span>
        </div>
        {evidence.length === 0 ? (
          <EmptyState compact title="暂无证据" description="运行工作流后生成字段级证据。" />
        ) : (
          <div className="evidence-list__scroll">
            {evidence.map((item) => {
              const sourceDoc = documents.find((doc) => doc.id === item.source_id);
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`evidence-item${item.id === activeEvidenceId ? ' evidence-item--active' : ''}`}
                  onClick={() => handleSelect(item)}
                  title={formatAny(item.bbox)}
                >
                  <div className="evidence-item__head">
                    <span className="evidence-source">
                      {sourceDoc?.type_label ?? documentTypeLabel(item.source_type)}
                      {item.page_no ? ` · P${item.page_no}` : ''}
                    </span>
                    <span className={`score${item.score < 0.9 ? ' score--warn' : ''}`}>
                      {formatPercent(item.score)}
                    </span>
                  </div>
                  <div className="evidence-item__quote">{item.quote}</div>
                  <div className="evidence-item__id">{item.id}</div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
