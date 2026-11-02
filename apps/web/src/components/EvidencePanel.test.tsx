import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CaseDocument, EvidenceItem } from '../types';
import { EvidencePanel } from './EvidencePanel';

afterEach(cleanup);

/** 伪造图片加载后的原始尺寸，jsdom 不会真正解码图片 */
function stubImageSize(img: HTMLImageElement, width: number, height: number): void {
  Object.defineProperty(img, 'naturalWidth', { value: width, configurable: true });
  Object.defineProperty(img, 'naturalHeight', { value: height, configurable: true });
}

function makeDocument(overrides: Partial<CaseDocument>): CaseDocument {
  return {
    id: 'doc_default',
    type: 'invoice',
    type_label: '发票',
    storage_key: 'doc.pdf',
    sha256: 'sha',
    page_count: 1,
    created_at: '2026-10-11T00:00:00',
    pages: [],
    fields: [],
    ...overrides,
  };
}

const invoice = makeDocument({
  id: 'doc_invoice',
  page_count: 2,
  pages: [
    {
      page_no: 1,
      ocr_text: '发票号码: INV-2026-1\n金额: 459.00',
      image_key: 'invoice#page1',
      image_url: '/cases/case_1/documents/doc_invoice/pages/1/image',
    },
    {
      page_no: 2,
      ocr_text: '第二页\n金额: 120.00',
      image_key: 'invoice#page2',
      image_url: '/cases/case_1/documents/doc_invoice/pages/2/image',
    },
  ],
});

const bank = makeDocument({
  id: 'doc_bank',
  type: 'bank_statement',
  type_label: '银行流水',
  pages: [{ page_no: 1, ocr_text: '交易金额: 120.00', image_key: null, image_url: null }],
});

const amountEvidence: EvidenceItem = {
  id: 'ev_amount',
  source_type: 'invoice',
  source_id: 'doc_invoice',
  page_no: 1,
  bbox: [59, 192, 122, 216],
  quote: '金额：459.00',
  score: 0.96,
};

const pageTwoEvidence: EvidenceItem = {
  id: 'ev_page2',
  source_type: 'invoice',
  source_id: 'doc_invoice',
  page_no: 2,
  bbox: [10, 20, 30, 40],
  quote: '金额：120.00',
  score: 0.92,
};

const bankEvidence: EvidenceItem = {
  id: 'ev_bank',
  source_type: 'bank_statement',
  source_id: 'doc_bank',
  page_no: 1,
  bbox: null,
  quote: '金额：120.00',
  score: 0.9,
};

describe('EvidencePanel 证据框选', () => {
  it('把像素 bbox 渲染为页面定位框并标记当前证据', () => {
    const { container } = render(
      <EvidencePanel
        documents={[invoice]}
        evidence={[amountEvidence]}
        activeEvidenceId="ev_amount"
        onSelectEvidence={vi.fn()}
      />,
    );

    const img = screen.getByRole('img') as HTMLImageElement;
    stubImageSize(img, 900, 340);
    fireEvent.load(img);

    const rect = container.querySelector('rect.evidence-box');
    expect(rect).not.toBeNull();
    expect(rect?.getAttribute('x')).toBe('59');
    expect(rect?.getAttribute('y')).toBe('192');
    expect(rect?.getAttribute('width')).toBe('63');
    expect(rect?.getAttribute('height')).toBe('24');
    expect(rect?.classList.contains('evidence-box--active')).toBe(true);
  });

  it('把 0~1 归一化 bbox 按图片尺寸换算', () => {
    const normalized: EvidenceItem = { ...amountEvidence, id: 'ev_norm', bbox: [0.1, 0.2, 0.4, 0.3] };
    const { container } = render(
      <EvidencePanel
        documents={[invoice]}
        evidence={[normalized]}
        activeEvidenceId={null}
        onSelectEvidence={vi.fn()}
      />,
    );

    const img = screen.getByRole('img') as HTMLImageElement;
    stubImageSize(img, 900, 300);
    fireEvent.load(img);

    const rect = container.querySelector('rect.evidence-box');
    expect(rect?.getAttribute('x')).toBe('90');
    expect(rect?.getAttribute('y')).toBe('60');
    expect(rect?.getAttribute('width')).toBe('270');
    expect(rect?.getAttribute('height')).toBe('30');
  });

  it('切换页码后只显示该页的框', () => {
    const { container } = render(
      <EvidencePanel
        documents={[invoice]}
        evidence={[amountEvidence, pageTwoEvidence]}
        activeEvidenceId={null}
        onSelectEvidence={vi.fn()}
      />,
    );

    let img = screen.getByRole('img') as HTMLImageElement;
    stubImageSize(img, 900, 340);
    fireEvent.load(img);
    expect(container.querySelectorAll('rect.evidence-box')).toHaveLength(1);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: '2' } });
    expect(container.querySelectorAll('rect.evidence-box')).toHaveLength(0);

    img = screen.getByRole('img') as HTMLImageElement;
    stubImageSize(img, 900, 340);
    fireEvent.load(img);
    const rects = container.querySelectorAll('rect.evidence-box');
    expect(rects).toHaveLength(1);
    expect(rects[0].getAttribute('x')).toBe('10');
  });

  it('无 bbox 的证据切换文档后回退为文本高亮', () => {
    const onSelectEvidence = vi.fn();
    const { container } = render(
      <EvidencePanel
        documents={[invoice, bank]}
        evidence={[bankEvidence]}
        activeEvidenceId="ev_bank"
        onSelectEvidence={onSelectEvidence}
      />,
    );

    expect(container.querySelector('mark.quote-mark')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /金额：120\.00/ }));

    expect(onSelectEvidence).toHaveBeenCalledWith('ev_bank');
    const mark = container.querySelector('mark.quote-mark');
    expect(mark?.textContent).toBe('120.00');
  });
});
