"""字段标签与规则常量（避免规则模块与 documents 模块互相依赖）。"""

from __future__ import annotations

FIELD_LABELS: dict[str, str] = {
    "invoice_no": "发票号",
    "amount": "金额",
    "date": "日期",
    "vendor": "供应商",
    "tax_no": "税号",
    "title": "发票抬头",
    "category": "费用类型",
    "nights": "住宿晚数",
    "merchant": "商户",
    "payment_method": "支付方式",
    "transaction_time": "交易时间",
    "counterparty": "对方户名",
    "transaction_no": "交易单号",
    "approver": "审批人",
    "approval_result": "审批结果",
    "approved_at": "审批时间",
    "reason": "事由",
    "trip_start": "出差开始日期",
    "trip_end": "出差结束日期",
}

INVOICE_REQUIRED: tuple[str, ...] = ("invoice_no", "amount", "date", "vendor")
