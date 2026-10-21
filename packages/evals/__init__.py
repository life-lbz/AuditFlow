"""评测框架（文档 §19：数据集、指标、对照实验）。"""

from .metrics import (
    CATEGORY_POLICY_MAP,
    CaseOutcome,
    SuiteMetrics,
    compute_evidence_metrics,
    compute_field_metrics,
    compute_policy_recall,
    compute_suite_metrics,
)
from .runner import EvalReport, EvalRunner

__all__ = [
    "CATEGORY_POLICY_MAP",
    "CaseOutcome",
    "EvalReport",
    "EvalRunner",
    "SuiteMetrics",
    "compute_evidence_metrics",
    "compute_field_metrics",
    "compute_policy_recall",
    "compute_suite_metrics",
]
