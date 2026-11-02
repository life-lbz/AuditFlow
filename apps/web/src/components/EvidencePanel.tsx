import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { apiUrl } from '../api';
import { documentTypeLabel, formatPercent, formatAny } from '../format';
import type { CaseDocument, EvidenceItem } from '../types';
import { EmptyState } from './EmptyState';

type Box = [number, number, number, number];

/** 解析后端 bbox：兼容 [x1,y1,x2,y2] / [[...]] / {x,y,width,height} / JSON 字符串 */
function parseBoxes(raw: unknown): Box[] {
  if (raw == null) return [];
  let value: unknown = raw;
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (Array.isArray(value)) {
    if (value.length === 4 && value.every((item) => typeof item === 'number')) {
      return [[...value] as Box];
    }
    return value.flatMap((item) => parseBoxes(item));
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    const x1 = Number(obj.x1 ?? obj.left ?? obj.x ?? NaN);
    const y1 = Number(obj.y1 ?? obj.top ?? obj.y ?? NaN);
    const x2 = Number(obj.x2 ?? (obj.x != null && obj.width != null ? Number(obj.x) + Number(obj.width) : NaN));
    const y2 = Number(obj.y2 ?? (obj.y != null && obj.height != null ? Number(obj.y) + Number(obj.height) : NaN));
    if ([x1, y1, x2, y2].every((n) => Number.isFinite(n))) {
      return [[x1, y1, x2, y2]];
    }
  }
  return [];
}

/** 归一化坐标（0~1）按图片实际尺寸换算为像素坐标 */
function scaleBox(box: Box, width: number, height: number): Box {
  const [x1, y1, x2, y2] = box;
  if (width > 0 && height > 0 && Math.max(x1, y1, x2, y2) <= 1.5 && Math.min(x1, y1, x2, y2) >= 0) {
    return [x1 * width, y1 * height, x2 * width, y2 * height];
  }
  return box;
}

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
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null);

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

  useEffect(() => {
    setImageSize(null);
  }, [document?.id, pageNo]);

  const pageBoxes = useMemo(() => {
    if (!document || !page) return [];
    return evidence
      .filter(
        (item) =>
          item.source_id === document.id && (item.page_no ?? page.page_no) === page.page_no,
      )
      .flatMap((item) =>
        parseBoxes(item.bbox).map((box) => ({
          evidenceId: item.id,
          box: imageSize ? scaleBox(box, imageSize.width, imageSize.height) : box,
          active: item.id === activeEvidenceId,
        })),
      );
  }, [document, page, evidence, activeEvidenceId, imageSize]);

  const hasBoxEvidence = evidence.some((item) => parseBoxes(item.bbox).length > 0);

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
          <div className="evidence-hint">
            {hasBoxEvidence
              ? '点击右侧证据条目，页面上的框会高亮该字段所在区域；也可直接点击框切换证据。'
              : '点击右侧证据条目，可在页面文本中定位并高亮引用。'}
          </div>
        )}

        <div className="evidence-canvas">
          {page?.image_url ? (
            <div className="evidence-image">
              <div className="evidence-image__frame">
                <img
                  src={apiUrl(page.image_url)}
                  alt={`${document?.type_label ?? '文档'}第 ${page.page_no} 页`}
                  onLoad={(event) =>
                    setImageSize({
                      width: event.currentTarget.naturalWidth,
                      height: event.currentTarget.naturalHeight,
                    })
                  }
                />
                {imageSize && pageBoxes.length > 0 && (
                  <svg
                    className="evidence-overlay"
                    viewBox={`0 0 ${imageSize.width} ${imageSize.height}`}
                    preserveAspectRatio="none"
                  >
                    {pageBoxes.map(({ evidenceId, box, active }) => (
                      <rect
                        key={evidenceId}
                        x={box[0]}
                        y={box[1]}
                        width={Math.max(2, box[2] - box[0])}
                        height={Math.max(2, box[3] - box[1])}
                        className={`evidence-box${active ? ' evidence-box--active' : ''}`}
                        onClick={() => onSelectEvidence(evidenceId)}
                      >
                        <title>证据字段定位区域</title>
                      </rect>
                    ))}
                  </svg>
                )}
              </div>
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
              const hasBox = parseBoxes(item.bbox).length > 0;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`evidence-item${item.id === activeEvidenceId ? ' evidence-item--active' : ''}`}
                  onClick={() => handleSelect(item)}
                  title={hasBox ? `页面定位：${formatAny(item.bbox)}` : '该证据无坐标'}
                >
                  <div className="evidence-item__head">
                    <span className="evidence-source">
                      {sourceDoc?.type_label ?? documentTypeLabel(item.source_type)}
                      {item.page_no ? ` · P${item.page_no}` : ''}
                      {hasBox && <span className="evidence-tag">框选</span>}
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
