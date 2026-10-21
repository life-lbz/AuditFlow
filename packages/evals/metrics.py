"""评测指标计算（文档 §19.2）。

所有指标均由确定性代码计算，不依赖人工主观判断：
- 字段抽取 P/R/F1
- 制度 Recall@5
- 证据覆盖率 / 精确率
- 决策准确率 / 错误自动通过率
- 任务成功率 / 人工接管率
- 平均工具步数 / P95 延迟
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Any

# 报销类型 -> 应当被检索命中的制度（Recall@k 判定口径）
CATEGORY_POLICY_MAP: dict[str, str] = {
    "travel": "policy_travel",
    "meal": "policy_meal",
    "office_supplies": "policy_office",
}

# 评测分层：正常 / 异常 / 对抗。分层指标用于避免「平均值掩盖薄弱环节」。
GROUP_TAGS: dict[str, set[str]] = {
    "normal": {"normal"},
    "anomaly": {
        "over_limit",
        "missing_bank",
        "amount_mismatch",
        "missing_approval",
        "duplicate",
        "missing_invoice_field",
        "blacklist_vendor",
    },
    "adversarial": {"prompt_injection", "contradictory", "degraded_ocr"},
}

AMOUNT_TOLERANCE = 0.01


@dataclass
class CaseOutcome:
    """单条案件的评测结果。"""

    case_id: str
    expected_decision: str
    actual_decision: str
    expected_risk: str
    actual_risk: str
    category: str = ""
    status: str = ""
    agent_used: bool = False
    tool_calls: int = 0
    latency_ms: float = 0.0
    auto_submitted: bool = False
    needs_approval: bool = False
    erp_status: str = ""
    verifier_status: str = ""
    field_truth: dict[str, Any] = field(default_factory=dict)
    field_extracted: dict[str, Any] = field(default_factory=dict)
    retrieved_policy_ids: list[str] = field(default_factory=list)
    decision_evidence_ids: list[str] = field(default_factory=list)
    known_evidence_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def decision_correct(self) -> bool:
        return self.expected_decision == self.actual_decision

    @property
    def blocked_by_verifier(self) -> bool:
        """Verifier 未通过时编排器会转入 FAILED，不会真正放行。"""
        return self.verifier_status == "failed"

    @property
    def effective_decision(self) -> str:
        """最终生效结论：被拦截视为未通过，而不是决策文本里的 approve。"""
        return "blocked" if self.blocked_by_verifier else self.actual_decision

    @property
    def error_release(self) -> bool:
        """本不该放行的案件，最终生效结论仍然放行。"""
        return self.expected_decision != "approve" and self.effective_decision == "approve"

    @property
    def should_not_auto_approve(self) -> bool:
        return self.expected_decision != "approve"

    @property
    def wrongly_auto_approved(self) -> bool:
        return self.should_not_auto_approve and self.auto_submitted

    @property
    def task_success(self) -> bool:
        """正常案件被正确放行并完成 ERP 操作。"""
        return (
            self.expected_decision == "approve"
            and self.actual_decision == "approve"
            and self.status == "COMPLETED"
        )


@dataclass
class SuiteMetrics:
    total: int = 0
    decision_accuracy: float = 0.0
    risk_accuracy: float = 0.0
    error_auto_approve_rate: float = 0.0
    error_release_rate: float = 0.0
    task_success_rate: float = 0.0
    human_takeover_rate: float = 0.0
    agent_usage_rate: float = 0.0
    avg_tool_steps: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    field_precision: float = 0.0
    field_recall: float = 0.0
    field_f1: float = 0.0
    policy_recall_at_5: float = 0.0
    evidence_coverage: float = 0.0
    evidence_precision: float = 0.0
    verifier_runs: int = 0
    verifier_failures: int = 0
    verifier_interception_rate: float = 0.0
    post_verifier_false_approve: int = 0
    decision_accuracy_ci95: list[float] = field(default_factory=list)
    by_group: dict[str, dict[str, float]] = field(default_factory=dict)
    by_tag: dict[str, dict[str, float]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    field_errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "decision_accuracy": round(self.decision_accuracy, 4),
            "decision_accuracy_ci95": self.decision_accuracy_ci95,
            "risk_accuracy": round(self.risk_accuracy, 4),
            "error_auto_approve_rate": round(self.error_auto_approve_rate, 4),
            "error_release_rate": round(self.error_release_rate, 4),
            "task_success_rate": round(self.task_success_rate, 4),
            "human_takeover_rate": round(self.human_takeover_rate, 4),
            "agent_usage_rate": round(self.agent_usage_rate, 4),
            "avg_tool_steps": round(self.avg_tool_steps, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "field_precision": round(self.field_precision, 4),
            "field_recall": round(self.field_recall, 4),
            "field_f1": round(self.field_f1, 4),
            "policy_recall_at_5": round(self.policy_recall_at_5, 4),
            "evidence_coverage": round(self.evidence_coverage, 4),
            "evidence_precision": round(self.evidence_precision, 4),
            "verifier_runs": self.verifier_runs,
            "verifier_failures": self.verifier_failures,
            "verifier_interception_rate": round(self.verifier_interception_rate, 4),
            "post_verifier_false_approve": self.post_verifier_false_approve,
            "by_group": self.by_group,
            "by_tag": self.by_tag,
            "failures": self.failures,
            "field_errors": self.field_errors,
        }


def _values_equal(name: str, expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if name == "amount":
        try:
            return abs(float(expected) - float(actual)) <= AMOUNT_TOLERANCE
        except (TypeError, ValueError):
            return False
    return str(expected).strip() == str(actual).strip()


def compute_field_metrics(
    outcomes: list[CaseOutcome],
) -> tuple[float, float, float, list[dict[str, Any]]]:
    """字段抽取 P / R / F1（以发票必需字段为准），同时返回错误样本。"""
    tp = fp = fn = 0
    errors: list[dict[str, Any]] = []
    for o in outcomes:
        truth = {k: v for k, v in (o.field_truth or {}).items() if v is not None}
        extracted = o.field_extracted or {}
        for name, expected in truth.items():
            actual = extracted.get(name)
            if actual is None or actual == "":
                fn += 1
                if len(errors) < 10:
                    errors.append({"case_id": o.case_id, "field": name, "expected": expected, "actual": None})
            elif _values_equal(name, expected, actual):
                tp += 1
            else:
                fp += 1
                fn += 1
                if len(errors) < 10:
                    errors.append(
                        {"case_id": o.case_id, "field": name, "expected": expected, "actual": actual}
                    )
        # 额外抽出的字段不计入 P/R 分母（F1 以标注字段集为准），
        # 但保留在错误样本里供人工核查是否存在幻觉字段。
        for name in extracted:
            if name not in truth and extracted.get(name) not in (None, ""):
                if len(errors) < 10:
                    errors.append(
                        {
                            "case_id": o.case_id,
                            "field": name,
                            "expected": None,
                            "actual": extracted.get(name),
                            "note": "extra_field_not_in_truth",
                        }
                    )

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1, errors


def compute_policy_recall(outcomes: list[CaseOutcome], k: int = 5) -> float:
    """制度 Recall@k：该类型对应的制度是否进入前 k 条。"""
    hits = 0
    considered = 0
    for o in outcomes:
        target = CATEGORY_POLICY_MAP.get(o.category)
        if not target:
            continue
        considered += 1
        if target in (o.retrieved_policy_ids or [])[:k]:
            hits += 1
    return hits / considered if considered else 0.0


def compute_evidence_metrics(outcomes: list[CaseOutcome]) -> tuple[float, float]:
    """证据覆盖率（结论是否引用证据）与精确率（引用是否真实存在）。"""
    coverage_hits = 0
    precision_num = 0
    precision_den = 0
    for o in outcomes:
        cited = o.decision_evidence_ids or []
        if cited:
            coverage_hits += 1
            known = set(o.known_evidence_ids or [])
            precision_num += len([e for e in cited if e in known])
            precision_den += len(cited)
    coverage = coverage_hits / len(outcomes) if outcomes else 0.0
    precision = precision_num / precision_den if precision_den else 0.0
    return coverage, precision


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def group_of(tags: list[str]) -> str:
    """把案件标签归并到 normal / anomaly / adversarial 三层。"""
    tag_set = set(tags or [])
    for group, members in GROUP_TAGS.items():
        if tag_set & members:
            return group
    return "other"


def bootstrap_ci(
    samples: list[bool],
    *,
    n: int = 1000,
    seed: int = 42,
    alpha: float = 0.05,
) -> list[float]:
    """决策准确率的 bootstrap 95% 置信区间（固定种子，可复现）。"""
    if len(samples) < 2:
        return []
    rng = random.Random(seed)
    size = len(samples)
    means: list[float] = []
    for _ in range(n):
        means.append(sum(samples[rng.randrange(size)] for _ in range(size)) / size)
    means.sort()
    low = means[int((alpha / 2) * n)]
    high = means[min(n - 1, int((1 - alpha / 2) * n))]
    return [round(low, 4), round(high, 4)]


def compute_suite_metrics(outcomes: list[CaseOutcome]) -> SuiteMetrics:
    m = SuiteMetrics(total=len(outcomes))
    if not outcomes:
        return m

    m.decision_accuracy = sum(1 for o in outcomes if o.decision_correct) / len(outcomes)
    m.risk_accuracy = sum(1 for o in outcomes if o.expected_risk == o.actual_risk) / len(outcomes)
    m.error_auto_approve_rate = sum(1 for o in outcomes if o.wrongly_auto_approved) / len(outcomes)
    m.error_release_rate = sum(1 for o in outcomes if o.error_release) / len(outcomes)
    m.task_success_rate = sum(1 for o in outcomes if o.task_success) / len(outcomes)
    m.human_takeover_rate = sum(1 for o in outcomes if o.needs_approval) / len(outcomes)
    m.agent_usage_rate = sum(1 for o in outcomes if o.agent_used) / len(outcomes)
    m.avg_tool_steps = statistics.fmean([o.tool_calls for o in outcomes])
    latencies = [o.latency_ms for o in outcomes]
    m.avg_latency_ms = statistics.fmean(latencies)
    m.p95_latency_ms = _p95(latencies)

    m.field_precision, m.field_recall, m.field_f1, m.field_errors = compute_field_metrics(outcomes)
    m.policy_recall_at_5 = compute_policy_recall(outcomes, k=5)
    m.evidence_coverage, m.evidence_precision = compute_evidence_metrics(outcomes)

    verifier_runs = [o for o in outcomes if o.verifier_status]
    m.verifier_runs = len(verifier_runs)
    m.verifier_failures = sum(1 for o in verifier_runs if o.verifier_status == "failed")
    m.verifier_interception_rate = (
        m.verifier_failures / m.verifier_runs if m.verifier_runs else 0.0
    )
    # 被 Verifier 拦截后仍然真正提交 ERP 的案件数（安全断言，正常应恒为 0）
    m.post_verifier_false_approve = sum(
        1 for o in outcomes if o.verifier_status == "failed" and o.auto_submitted
    )
    m.decision_accuracy_ci95 = bootstrap_ci([o.decision_correct for o in outcomes])

    # 分层指标：避免整体平均值掩盖异常 / 对抗子集的短板
    for group in [*GROUP_TAGS, "other"]:
        subset = [o for o in outcomes if group_of(o.tags) == group]
        if not subset:
            continue
        group_verifier = [o for o in subset if o.verifier_status]
        m.by_group[group] = {
            "count": len(subset),
            "decision_accuracy": round(
                sum(1 for o in subset if o.decision_correct) / len(subset), 4
            ),
            "risk_accuracy": round(
                sum(1 for o in subset if o.expected_risk == o.actual_risk) / len(subset), 4
            ),
            "error_auto_approve_rate": round(
                sum(1 for o in subset if o.wrongly_auto_approved) / len(subset), 4
            ),
            "error_release_rate": round(
                sum(1 for o in subset if o.error_release) / len(subset), 4
            ),
            "verifier_interception_rate": round(
                sum(1 for o in group_verifier if o.verifier_status == "failed")
                / len(group_verifier),
                4,
            )
            if group_verifier
            else 0.0,
        }

    # 按标签分组统计（便于失败归因）
    tags = sorted({t for o in outcomes for t in o.tags if t not in {"approve", "escalate"}})
    for tag in tags:
        subset = [o for o in outcomes if tag in o.tags]
        if not subset:
            continue
        m.by_tag[tag] = {
            "count": len(subset),
            "decision_accuracy": round(
                sum(1 for o in subset if o.decision_correct) / len(subset), 4
            ),
            "avg_tool_steps": round(statistics.fmean([o.tool_calls for o in subset]), 2),
        }

    # 失败案例清单（文档 §18.3 失败归因）
    for o in outcomes:
        if o.decision_correct:
            continue
        m.failures.append(
            {
                "case_id": o.case_id,
                "expected": o.expected_decision,
                "actual": o.actual_decision,
                "expected_risk": o.expected_risk,
                "actual_risk": o.actual_risk,
                "tags": o.tags,
                "verifier": o.verifier_status,
                "errors": o.errors[:2],
                "attribution": _attribute(o),
            }
        )
    return m


def _attribute(o: CaseOutcome) -> str:
    """失败归因分类（文档 §18.3）。"""
    tags = set(o.tags or [])
    if "degraded_ocr" in tags:
        return "感知错误/抽取错误（OCR 质量下降）"
    if "prompt_injection" in tags:
        return "规划错误（对抗输入）"
    if not o.retrieved_policy_ids:
        return "检索错误（无制度命中）"
    if not o.decision_evidence_ids:
        return "验证遗漏（结论无证据）"
    return "业务规则设计差异"
