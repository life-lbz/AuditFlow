"""确定性规则引擎测试（文档 §9）：8 条规则逐条校验通过/失败/跳过，以及风险等级边界。"""

from __future__ import annotations

from typing import Any

from packages.rules.engine import (
    RuleContext,
    RuleEngine,
    RuleResult,
    RuleStatus,
    Severity,
    assess_risk,
    default_rules,
)
from packages.rules.rules.amount_matches_bank import AmountMatchesBank
from packages.rules.rules.amount_matches_receipt import AmountMatchesReceipt
from packages.rules.rules.approval_required import ApprovalRequired
from packages.rules.rules.category_limit import CategoryLimit
from packages.rules.rules.date_in_range import DateInRange
from packages.rules.rules.duplicate_invoice import DuplicateInvoice
from packages.rules.rules.invoice_required_fields import InvoiceRequiredFields
from packages.rules.rules.vendor_check import VendorCheck

FULL_INVOICE: dict[str, Any] = {
    "invoice_no": "INV-2026-001",
    "amount": 100.0,
    "date": "2026-10-05",
    "vendor": "中国铁路",
}


def build_ctx(**overrides: Any) -> RuleContext:
    """构造最小可用的规则上下文，测试只覆盖与断言相关的字段。"""
    base: dict[str, Any] = {
        "case_id": "case_test_001",
        "employee_id": "emp_001",
        "category": "meal",
        "amount": 100.0,
    }
    base.update(overrides)
    return RuleContext(**base)


def doc_ctx(
    documents: dict[str, dict[str, Any]],
    *,
    category: str = "meal",
    amount: float = 100.0,
    fields: dict[str, Any] | None = None,
    **overrides: Any,
) -> RuleContext:
    """按 {文档类型: 字段集} 组装上下文（文档 id 由类型确定性生成）。"""
    document_ids = {doc_type: f"doc_{doc_type}" for doc_type in documents}
    by_document = {
        document_ids[doc_type]: dict(document_fields)
        for doc_type, document_fields in documents.items()
    }
    return build_ctx(
        category=category,
        amount=amount,
        fields=fields or {},
        document_types=list(documents),
        document_ids=document_ids,
        by_document=by_document,
        **overrides,
    )


def make_result(
    rule_name: str,
    status: RuleStatus = RuleStatus.PASSED,
    severity: Severity = Severity.NONE,
) -> RuleResult:
    return RuleResult(rule_name=rule_name, status=status, severity=severity)


# ---------------------------------------------------------------------------
# 1) invoice_required_fields
# ---------------------------------------------------------------------------


async def test_invoice_required_fields_passed():
    """发票字段齐全且金额为正数时通过。"""
    ctx = doc_ctx({"invoice": FULL_INVOICE})
    result = await InvoiceRequiredFields().evaluate(ctx)

    assert result.rule_name == "invoice_required_fields"
    assert result.status == RuleStatus.PASSED
    assert result.severity == Severity.NONE
    assert "完整" in result.message
    assert result.result_json["amount"] == 100.0


async def test_invoice_required_fields_missing_invoice_document():
    """缺少发票文档时以 HIGH 失败，并提示缺少报销依据。"""
    ctx = doc_ctx({"receipt": {"amount": 100.0}})
    result = await InvoiceRequiredFields().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "缺少发票文档" in result.message
    assert result.result_json["has_invoice"] is False


async def test_invoice_required_fields_missing_vendor():
    """发票缺少供应商字段时失败，消息列出缺失字段并给出证据。"""
    invoice = {k: v for k, v in FULL_INVOICE.items() if k != "vendor"}
    ctx = doc_ctx(
        {"invoice": invoice},
        evidence_by_field={"vendor": "ev_vendor"},
    )
    result = await InvoiceRequiredFields().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "供应商" in result.message
    assert result.result_json["missing_fields"] == ["vendor"]
    assert result.evidence_ids == ["ev_vendor"]


# ---------------------------------------------------------------------------
# 2) amount_matches_receipt
# ---------------------------------------------------------------------------


async def test_amount_matches_receipt_passed():
    """发票与收据金额在 0.01 容差内一致时通过。"""
    ctx = doc_ctx({"invoice": FULL_INVOICE, "receipt": {"amount": 100.005}})
    result = await AmountMatchesReceipt().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert result.severity == Severity.NONE
    assert "金额一致" in result.message
    assert result.result_json["receipt_amount"] == 100.005


async def test_amount_matches_receipt_mismatch():
    """收据金额与发票不一致时以 HIGH 失败，并给出差额。"""
    ctx = doc_ctx({"invoice": FULL_INVOICE, "receipt": {"amount": 120.5}})
    result = await AmountMatchesReceipt().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "不一致" in result.message
    assert result.result_json["diff"] == -20.5


async def test_amount_matches_receipt_tolerance_boundary():
    """差额恰好 0.01 元视为一致，超过 0.01 元才失败（浮点差值不得误判）。"""
    within = await AmountMatchesReceipt().evaluate(
        doc_ctx({"invoice": FULL_INVOICE, "receipt": {"amount": 100.01}})
    )
    assert within.status == RuleStatus.PASSED

    beyond = await AmountMatchesReceipt().evaluate(
        doc_ctx({"invoice": FULL_INVOICE, "receipt": {"amount": 100.02}})
    )
    assert beyond.status == RuleStatus.FAILED


async def test_amount_matches_receipt_skipped_without_receipt():
    """无收据文档时跳过，不误判为失败。"""
    ctx = doc_ctx({"invoice": FULL_INVOICE})
    result = await AmountMatchesReceipt().evaluate(ctx)

    assert result.status == RuleStatus.SKIPPED
    assert result.severity == Severity.NONE
    assert "无收据文档" in result.message


# ---------------------------------------------------------------------------
# 3) amount_matches_bank
# ---------------------------------------------------------------------------


async def test_amount_matches_bank_passed():
    """发票与银行流水金额一致时通过。"""
    ctx = doc_ctx({"invoice": FULL_INVOICE, "bank_statement": {"amount": 100.0}})
    result = await AmountMatchesBank().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert result.severity == Severity.NONE
    assert "流水金额一致" in result.message


async def test_amount_matches_bank_mismatch():
    """流水金额不一致时以 HIGH 失败并给出差额。"""
    ctx = doc_ctx({"invoice": FULL_INVOICE, "bank_statement": {"amount": 99.0}})
    result = await AmountMatchesBank().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "不一致" in result.message
    assert result.result_json["diff"] == 1.0


async def test_amount_matches_bank_tolerance_boundary():
    """差额恰好 0.01 元视为一致，超过 0.01 元才失败（浮点差值不得误判）。"""
    within = await AmountMatchesBank().evaluate(
        doc_ctx({"invoice": FULL_INVOICE, "bank_statement": {"amount": 100.01}})
    )
    assert within.status == RuleStatus.PASSED

    beyond = await AmountMatchesBank().evaluate(
        doc_ctx({"invoice": FULL_INVOICE, "bank_statement": {"amount": 100.02}})
    )
    assert beyond.status == RuleStatus.FAILED


async def test_amount_matches_bank_skipped_without_statement():
    """无银行流水文档时跳过。"""
    ctx = doc_ctx({"invoice": FULL_INVOICE})
    result = await AmountMatchesBank().evaluate(ctx)

    assert result.status == RuleStatus.SKIPPED
    assert "无银行流水文档" in result.message


# ---------------------------------------------------------------------------
# 4) date_in_range
# ---------------------------------------------------------------------------


async def test_date_in_range_passed():
    """票据日期落在出差区间内时通过。"""
    ctx = doc_ctx(
        {
            "invoice": {**FULL_INVOICE, "date": "2026-10-05"},
            "approval_email": {"trip_start": "2026-10-04", "trip_end": "2026-10-06"},
        }
    )
    result = await DateInRange().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert result.severity == Severity.NONE
    assert "范围内" in result.message
    assert result.result_json["ticket_date"] == "2026-10-05"


async def test_date_in_range_out_of_range():
    """票据日期超出区间（超过 1 天浮动）时以 MEDIUM 失败。"""
    ctx = doc_ctx(
        {
            "invoice": {**FULL_INVOICE, "date": "2026-11-01"},
            "approval_email": {"trip_start": "2026-10-04", "trip_end": "2026-10-06"},
        }
    )
    result = await DateInRange().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.MEDIUM
    assert "不在出差行程" in result.message
    assert result.result_json["trip_start"] == "2026-10-04"
    assert result.result_json["trip_end"] == "2026-10-06"


async def test_date_in_range_skipped_without_trip_dates():
    """审批邮件缺少行程起止日期时跳过（无法确定合理区间）。"""
    ctx = doc_ctx(
        {
            "invoice": FULL_INVOICE,
            "approval_email": {"approver": "李经理"},
        }
    )
    result = await DateInRange().evaluate(ctx)

    assert result.status == RuleStatus.SKIPPED
    assert "缺少出差起止日期" in result.message


# ---------------------------------------------------------------------------
# 5) duplicate_invoice
# ---------------------------------------------------------------------------


async def test_duplicate_invoice_passed():
    """历史案件中无相同发票号或金额/日期/供应商组合时通过。"""
    ctx = doc_ctx(
        {"invoice": FULL_INVOICE},
        history=[
            {
                "case_id": "case_old_001",
                "invoice_no": "INV-OTHER",
                "amount": 300.0,
                "date": "2026-01-01",
                "vendor": "京东",
            }
        ],
    )
    result = await DuplicateInvoice().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert result.severity == Severity.NONE
    assert "未发现重复报销" in result.message
    assert result.result_json["checked_history"] == 1


async def test_duplicate_invoice_failed_by_invoice_no():
    """历史案件发票号相同即判定重复，以 HIGH 失败并引用历史证据。"""
    ctx = doc_ctx(
        {"invoice": FULL_INVOICE},
        history=[
            {"case_id": "case_old_002", "invoice_no": "INV-2026-001", "evidence_id": "ev_hist_1"}
        ],
    )
    result = await DuplicateInvoice().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "疑似重复报销" in result.message
    assert result.result_json["duplicate_of"] == ["case_old_002"]
    assert result.result_json["match_dimensions"] == ["invoice_no"]
    assert "ev_hist_1" in result.evidence_ids


async def test_duplicate_invoice_failed_by_amount_date_vendor():
    """发票号不同但金额+日期+供应商完全一致时同样判为重复。"""
    ctx = doc_ctx(
        {"invoice": FULL_INVOICE},
        history=[
            {
                "case_id": "case_old_003",
                "invoice_no": "INV-OTHER",
                "amount": 100.0,
                "date": "2026/10/05",
                "vendor": "中国铁路",
            }
        ],
    )
    result = await DuplicateInvoice().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert result.result_json["match_dimensions"] == ["amount+date+vendor"]


async def test_duplicate_invoice_skipped_without_keys():
    """既无发票号也无法组成金额/日期/供应商三元组时跳过。"""
    ctx = doc_ctx({"invoice": {"vendor": "中国铁路"}})
    result = await DuplicateInvoice().evaluate(ctx)

    assert result.status == RuleStatus.SKIPPED
    assert "无法做重复判断" in result.message


# ---------------------------------------------------------------------------
# 6) category_limit
# ---------------------------------------------------------------------------


async def test_category_limit_travel_passed():
    """差旅住宿按晚数计算均价未超每晚上限且总额未超单次上限时通过。"""
    ctx = doc_ctx(
        {"invoice": {"amount": 1200.0, "nights": 3}},
        category="travel",
        policy_limits={"hotel_nightly": 500.0, "category_cap": 2000.0},
    )
    result = await CategoryLimit().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert result.severity == Severity.NONE
    assert "未超制度额度" in result.message
    assert result.result_json["nightly_limit"] == 500.0


async def test_category_limit_travel_per_night_exceeded():
    """住宿均价超过每晚上限时以 MEDIUM 失败。"""
    ctx = doc_ctx(
        {"invoice": {"amount": 1600.0, "nights": 2}},
        category="travel",
        policy_limits={"hotel_nightly": 500.0},
    )
    result = await CategoryLimit().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.MEDIUM
    assert "超出制度额度" in result.message
    assert result.result_json["per_night"] == 800.0


async def test_category_limit_meal_exceeded():
    """餐饮人均费用超限时以 MEDIUM 失败。"""
    ctx = doc_ctx(
        {"invoice": {"amount": 300.0, "attendees": 2}},
        category="meal",
        policy_limits={"meal_per_person": 100.0},
    )
    result = await CategoryLimit().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.MEDIUM
    assert "餐饮人均费用" in result.message
    assert result.result_json["per_person"] == 150.0


async def test_category_limit_meal_passed():
    """餐饮人均未超限时通过。"""
    ctx = doc_ctx(
        {"invoice": {"amount": 300.0, "attendees": 3}},
        category="meal",
        policy_limits={"meal_per_person": 100.0},
    )
    result = await CategoryLimit().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert "餐饮人均费用未超制度额度" in result.message


async def test_category_limit_office_exceeded():
    """办公用品单件金额超限时以 MEDIUM 失败。"""
    ctx = doc_ctx(
        {"invoice": {"amount": 2500.0}},
        category="office_supplies",
        policy_limits={"office_single_item": 2000.0},
    )
    result = await CategoryLimit().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.MEDIUM
    assert "办公用品单件金额" in result.message


async def test_category_limit_skipped_without_policy():
    """未检索到额度条款时跳过（额度必须来自制度，不能由代码臆断）。"""
    ctx = doc_ctx({"invoice": {"amount": 9999.0}}, category="travel")
    result = await CategoryLimit().evaluate(ctx)

    assert result.status == RuleStatus.SKIPPED
    assert "未检索到差旅额度条款" in result.message


# ---------------------------------------------------------------------------
# 7) vendor_check
# ---------------------------------------------------------------------------


async def test_vendor_check_blacklisted():
    """供应商命中黑名单关键字时以 HIGH 失败。"""
    ctx = doc_ctx(
        {"invoice": {"vendor": "鑫鑫贸易有限公司"}},
        vendor_blacklist=["鑫鑫贸易"],
    )
    result = await VendorCheck().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "命中风险名单" in result.message
    assert result.result_json["matched_rule"] == "鑫鑫贸易"


async def test_vendor_check_whitelisted():
    """供应商命中白名单时通过。"""
    ctx = doc_ctx(
        {"invoice": {"vendor": "华住酒店"}},
        vendor_whitelist=["华住酒店"],
    )
    result = await VendorCheck().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert "白名单" in result.message


async def test_vendor_check_unlisted_passed_with_notice():
    """未命中任何名单时仍通过，但标记为非白名单需关注。"""
    ctx = doc_ctx({"invoice": {"vendor": "某某小卖部"}})
    result = await VendorCheck().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert "未命中风险名单" in result.message
    assert result.result_json["in_whitelist"] is False


async def test_vendor_check_skipped_without_vendor():
    """未抽取到供应商名称时跳过。"""
    ctx = doc_ctx({"invoice": {"amount": 100.0}})
    result = await VendorCheck().evaluate(ctx)

    assert result.status == RuleStatus.SKIPPED
    assert "未抽取到供应商" in result.message


# ---------------------------------------------------------------------------
# 8) approval_required
# ---------------------------------------------------------------------------


async def test_approval_required_not_needed():
    """非差旅且金额未达阈值时不要求审批。"""
    ctx = doc_ctx(
        {"invoice": {"amount": 500.0}},
        category="meal",
        policy_limits={"approval_threshold": 1000.0},
    )
    result = await ApprovalRequired().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert "未触发审批阈值" in result.message
    assert result.result_json["approval_required"] is False


async def test_approval_required_travel_missing_email():
    """差旅案件缺少审批邮件时以 HIGH 失败。"""
    ctx = doc_ctx({"invoice": {"amount": 800.0}}, category="travel")
    result = await ApprovalRequired().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "必须提供出差审批邮件" in result.message
    assert result.result_json["missing"] == ["approval_email"]


async def test_approval_required_threshold_missing_email():
    """金额达到审批阈值却无审批记录时以 HIGH 失败。"""
    ctx = doc_ctx(
        {"invoice": {"amount": 1500.0}},
        category="meal",
        policy_limits={"approval_threshold": 1000.0},
    )
    result = await ApprovalRequired().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "达到审批阈值" in result.message


async def test_approval_required_approved():
    """审批邮件结果为「通过」时通过，并带出审批人。"""
    ctx = doc_ctx(
        {
            "invoice": {"amount": 800.0},
            "approval_email": {"approver": "李经理", "approval_result": "通过"},
        },
        category="travel",
    )
    result = await ApprovalRequired().evaluate(ctx)

    assert result.status == RuleStatus.PASSED
    assert "审批有效" in result.message
    assert result.result_json["approver"] == "李经理"


async def test_approval_required_rejected():
    """审批结果为「驳回」时以 HIGH 失败。"""
    ctx = doc_ctx(
        {
            "invoice": {"amount": 800.0},
            "approval_email": {"approver": "李经理", "approval_result": "驳回"},
        },
        category="travel",
    )
    result = await ApprovalRequired().evaluate(ctx)

    assert result.status == RuleStatus.FAILED
    assert result.severity == Severity.HIGH
    assert "否决" in result.message


# ---------------------------------------------------------------------------
# 风险评分边界（文档 §2.3）
# ---------------------------------------------------------------------------


def test_assess_risk_low():
    """金额小、置信度高且有制度证据时评为 low。"""
    ctx = build_ctx(
        amount=100.0,
        confidences={"amount": 0.95},
        policy_chunks=[{"content": "报销制度条款"}],
    )
    risk = assess_risk(ctx, [make_result("invoice_required_fields")])

    assert risk.level == "low"
    assert risk.score < 0.30
    assert risk.components["policy_ambiguity"] == 0.0
    assert risk.components["duplicate_suspicion"] == 0.0


def test_assess_risk_medium():
    """无制度命中、低置信度且金额触顶时总分落入 medium 区间。"""
    ctx = build_ctx(
        amount=5000.0,
        confidences={"amount": 0.4},
        policy_chunks=[],
    )
    risk = assess_risk(ctx, [make_result("invoice_required_fields")])

    assert risk.level == "medium"
    assert 0.30 <= risk.score < 0.60
    assert risk.components["policy_ambiguity"] == 1.0
    assert risk.components["low_extraction_confidence"] == 1.0


def test_assess_risk_high_by_score():
    """多项中危失败叠加后总分超过高阈值，评为 high。"""
    ctx = build_ctx(
        amount=1000.0,
        policy_limits={"category_cap": 1000.0},
        confidences={"amount": 0.9},
        policy_chunks=[],
    )
    results = [
        make_result("amount_matches_receipt", RuleStatus.FAILED, Severity.MEDIUM),
        make_result("duplicate_invoice", RuleStatus.FAILED, Severity.MEDIUM),
        make_result("vendor_check", RuleStatus.FAILED, Severity.MEDIUM),
    ]
    risk = assess_risk(ctx, results)

    assert risk.level == "high"
    assert risk.score >= 0.60
    assert risk.components["document_mismatch"] == 0.6


def test_assess_risk_high_severity_forces_high_level():
    """即使基础分很低，只要存在 HIGH 严重度失败也会被抬到 high。"""
    ctx = build_ctx(
        amount=1.0,
        confidences={"amount": 0.99},
        policy_chunks=[{"content": "制度条款"}],
    )
    results = [make_result("invoice_required_fields", RuleStatus.FAILED, Severity.HIGH)]
    risk = assess_risk(ctx, results)

    assert risk.level == "high"
    assert risk.score >= 0.60


def test_assess_risk_threshold_boundaries():
    """验证 low/medium/high 的三段阈值比较逻辑（用自定义阈值避开浮点相等）。"""
    ctx = build_ctx(
        amount=100.0,
        confidences={"amount": 0.95},
        policy_chunks=[{"content": "制度条款"}],
    )
    base_score = assess_risk(ctx, []).score
    assert 0.0 < base_score < 0.05

    # 低于低阈值 -> low
    assert assess_risk(ctx, [], low_threshold=0.01, high_threshold=0.50).level == "low"
    # 高于低阈值但低于高阈值 -> medium
    assert assess_risk(ctx, [], low_threshold=0.001, high_threshold=0.50).level == "medium"
    # 高于高阈值 -> high
    assert assess_risk(ctx, [], low_threshold=0.0001, high_threshold=0.001).level == "high"


# ---------------------------------------------------------------------------
# 引擎整体
# ---------------------------------------------------------------------------


async def test_rule_engine_runs_all_default_rules_in_order():
    """默认规则集按固定顺序执行 8 条规则，且完整合规案件全部通过。"""
    ctx = doc_ctx(
        {
            "invoice": FULL_INVOICE,
            "receipt": {"amount": 100.0},
            "bank_statement": {"amount": 100.0},
            "approval_email": {
                "trip_start": "2026-10-04",
                "trip_end": "2026-10-06",
                "approver": "李经理",
                "approval_result": "通过",
            },
        },
        category="travel",
        policy_limits={"hotel_nightly": 500.0, "category_cap": 2000.0},
        vendor_whitelist=["中国铁路"],
        policy_chunks=[{"content": "差旅报销制度条款"}],
    )
    outcome = await RuleEngine().run(ctx)

    assert len(outcome.results) == 8
    assert [r.rule_name for r in outcome.results] == [r.name for r in default_rules()]
    assert [r.status for r in outcome.results] == [RuleStatus.PASSED] * 8
    assert outcome.failed == []
    assert outcome.risk.level == "low"
