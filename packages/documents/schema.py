"""单据字段规范（文档 §7.2：各文档类型必需字段）。"""

from __future__ import annotations

from ..gateway.base import DocumentType

# 各类型必需字段（缺失即视为材料不完整，进入 need_info 路径）
REQUIRED_FIELDS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.INVOICE: ("invoice_no", "amount", "date", "vendor"),
    DocumentType.RECEIPT: ("amount", "date", "merchant"),
    DocumentType.BANK_STATEMENT: ("transaction_time", "amount", "counterparty"),
    DocumentType.APPROVAL_EMAIL: ("approver", "approval_result"),
    DocumentType.POLICY_PDF: (),
    DocumentType.UNKNOWN: (),
}

# 字段中文名（用于面向前端与员工的说明文案）
FIELD_LABELS: dict[str, str] = {
    "invoice_no": "发票号",
    "amount": "金额",
    "date": "日期",
    "vendor": "供应商",
    "tax_no": "税号",
    "title": "发票抬头",
    "category": "费用类型",
    "merchant": "商户",
    "payment_method": "支付方式",
    "transaction_time": "交易时间",
    "counterparty": "对方户名",
    "transaction_no": "交易单号",
    "approver": "审批人",
    "approval_result": "审批结果",
    "approved_at": "审批时间",
    "reason": "事由",
}

# 案件类型 -> 期望的必备文档类型（用于缺材料判断，文档 §2.1 + 附录 C.3）
REQUIRED_DOCUMENTS: dict[str, tuple[DocumentType, ...]] = {
    # 差旅：发票 + 出差审批邮件 + 银行流水（缺流水应走 need_info）
    "travel": (DocumentType.INVOICE, DocumentType.APPROVAL_EMAIL, DocumentType.BANK_STATEMENT),
    "meal": (DocumentType.INVOICE,),
    "office_supplies": (DocumentType.INVOICE,),
}

DOCUMENT_TYPE_LABELS: dict[str, str] = {
    DocumentType.INVOICE.value: "发票",
    DocumentType.RECEIPT.value: "收据",
    DocumentType.BANK_STATEMENT.value: "银行流水",
    DocumentType.APPROVAL_EMAIL.value: "审批邮件",
    DocumentType.POLICY_PDF.value: "制度文件",
    DocumentType.UNKNOWN.value: "未知类型",
}

# 文件名/内容关键词 -> 类型（类型识别规则，文档 §7.1）
_TYPE_KEYWORDS: tuple[tuple[DocumentType, tuple[str, ...]], ...] = (
    (DocumentType.BANK_STATEMENT, ("bank", "流水", "账户明细", "对账")),
    (DocumentType.APPROVAL_EMAIL, ("email", "mail", "审批", "邮件")),
    (DocumentType.RECEIPT, ("receipt", "收据", "小票")),
    (DocumentType.POLICY_PDF, ("policy", "制度", "规定", "办法")),
    (DocumentType.INVOICE, ("invoice", "发票", "增值税")),
)


def detect_document_type(filename: str, text: str = "", declared: str | None = None) -> DocumentType:
    """识别文档类型：显式声明优先，其次文件名与内容关键词。"""
    if declared:
        try:
            dt = DocumentType(declared.strip().lower())
            if dt != DocumentType.UNKNOWN:
                return dt
        except ValueError:
            pass

    haystack = f"{filename}\n{text[:800]}".lower()
    for doc_type, keywords in _TYPE_KEYWORDS:
        if any(k.lower() in haystack for k in keywords):
            return doc_type
    return DocumentType.UNKNOWN


def missing_fields(doc_type: DocumentType, present: dict[str, object]) -> list[str]:
    """返回缺失的必需字段中文名列表。"""
    required = REQUIRED_FIELDS.get(doc_type, ())
    return [FIELD_LABELS.get(f, f) for f in required if not present.get(f)]
