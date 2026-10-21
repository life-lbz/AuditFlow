"""本地确定性模型网关（StubModelGateway）。

用途：在没有任何 GPU / 模型服务的情况下，以**确定性启发式**模拟模型能力，
使 AuditFlow 的编排、规则、工具、验证、评测全流程可以真实端到端跑通并进入 CI。

与真实模型的分工：
- 这里实现的是「模型能力的最小可用替身」，输出结构、置信度语义、错误语义与真实模型一致。
- 迁移到 AutoDL 后由 RemoteModelGateway 替换，接口不变。
- 所有启发式逻辑集中在 _extract_* / _decide_* 中，便于对照真实模型效果做消融实验。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from typing import Any

from .base import (
    AgentAction,
    DocumentType,
    ExtractionField,
    ExtractionRequest,
    ExtractionResult,
    ModelCallRecord,
    ModelGateway,
    VerificationRequest,
    VerificationResult,
)

# ---------------------------------------------------------------------------
# 字段抽取正则表：与 data 合成数据生成器约定的「标准票据文本格式」配套
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(
    r"(?:金额|合计金额|价税合计|总额|小写|实付|实收)\s*[:：]?\s*(?:¥|￥|RMB)?\s*([0-9][0-9,]*\.?[0-9]{0,2})"
)
_AMOUNT_LOOSE_RE = re.compile(r"(?:¥|￥)\s*([0-9][0-9,]*\.?[0-9]{0,2})")
_DATE_RE = re.compile(r"(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)")
_DATETIME_RE = re.compile(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)")
_INVOICE_NO_RE = re.compile(r"(?:发票号码|发票号|票据号码|Invoice\s*No\.?)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_]{3,})")
_VENDOR_RE = re.compile(r"(?:销售方|供应商|商户名称|商户|商家|收款方|对方户名|对方)\s*[:：]?\s*([^\n,，;；]+)")
_TAX_NO_RE = re.compile(r"(?:纳税人识别号|税号)\s*[:：]?\s*([A-Z0-9]{15,20})")
_TITLE_RE = re.compile(r"(?:购买方名称|购买方|抬头|付款方)\s*[:：]?\s*([^\n,，;；]+)")
_APPROVER_RE = re.compile(r"(?:审批人|审核人|批准人)\s*[:：]?\s*([^\n,，;；]+)")
_APPROVAL_RESULT_RE = re.compile(r"(?:审批结果|审批结论|审批意见)\s*[:：]?\s*([^\n]+)")
_APPROVAL_REASON_RE = re.compile(r"(?:事由|申请事由|出差事由)\s*[:：]?\s*([^\n]+)")
_CATEGORY_RE = re.compile(r"(?:费用类型|报销类型|费用类别)\s*[:：]?\s*([^\n,，;；]+)")
_PAYMENT_RE = re.compile(r"(?:支付方式|付款方式)\s*[:：]?\s*([^\n,，;；]+)")
_TXN_NO_RE = re.compile(r"(?:交易单号|交易号|流水号|凭证号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_]{3,})")
_NIGHTS_RE = re.compile(r"(\d+)\s*晚")
_ATTENDEES_RE = re.compile(r"(\d+)\s*人")
_TRIP_START_RE = re.compile(r"出差开始日期\s*[:：]?\s*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)")
_TRIP_END_RE = re.compile(r"出差结束日期\s*[:：]?\s*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)")

# OCR 退化标记：合成数据在退化样本中注入，stub 据此下调置信度（文档 §7.4 双重校验思想）
_DEGRADE_MARKERS = ("[OCR_LOW_QUALITY]", "[BLUR]", "[PARTIAL]")

# 各文档类型必需字段（文档 §7.2）
REQUIRED_FIELDS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.INVOICE: ("invoice_no", "amount", "date", "vendor"),
    DocumentType.RECEIPT: ("amount", "date", "merchant"),
    DocumentType.BANK_STATEMENT: ("transaction_time", "amount", "counterparty"),
    DocumentType.APPROVAL_EMAIL: ("approver", "approval_result"),
    DocumentType.POLICY_PDF: (),
    DocumentType.UNKNOWN: (),
}


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _normalize_date(raw: str) -> str:
    s = raw.strip().replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
    parts = s.split("-")
    if len(parts) != 3:
        return s
    y, m, d = parts
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class StubModelGateway(ModelGateway):
    """确定性替身模型：输出结构与真实模型一致，行为可复现。"""

    provider = "stub"
    model = "stub-deterministic-v1"
    prompt_version = "stub-v1"

    # ------------------------------------------------------------------
    # 文档结构化抽取
    # ------------------------------------------------------------------
    async def extract_structured(self, request: ExtractionRequest) -> ExtractionResult:
        started = time.perf_counter()
        text = "\n".join(request.pages or [])
        degraded = any(m in text for m in _DEGRADE_MARKERS)
        # 退化样本：去掉标记后仍可抽取，但置信度按文档打折
        cleaned = text
        for marker in _DEGRADE_MARKERS:
            cleaned = cleaned.replace(marker, "")

        fields: dict[str, ExtractionField] = {}
        doc_type = request.document_type

        if doc_type in (DocumentType.INVOICE, DocumentType.RECEIPT):
            self._extract_common_billing(cleaned, fields, doc_type)
        elif doc_type == DocumentType.BANK_STATEMENT:
            self._extract_bank(cleaned, fields)
        elif doc_type == DocumentType.APPROVAL_EMAIL:
            self._extract_approval(cleaned, fields)

        confidence_scale = 0.6 if degraded else 1.0
        for f in fields.values():
            f.confidence = round(min(1.0, f.confidence * confidence_scale), 4)
            if degraded:
                f.source = "fused-low"

        evidence = [
            {
                "field": name,
                "page_no": f.page_no,
                "bbox": f.bbox,
                "quote": self._quote_for(cleaned, name, f.value),
            }
            for name, f in fields.items()
        ]

        record = ModelCallRecord(
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            input_hash=_hash_text(f"{doc_type.value}:{text}"),
            output_hash=_hash_text(repr(sorted((k, str(v.value)) for k, v in fields.items()))),
            prompt_tokens=len(cleaned) // 4,
            completion_tokens=sum(len(str(f.value)) for f in fields.values()) // 4,
            latency_ms=int((time.perf_counter() - started) * 1000),
            degraded=degraded,
        )
        return ExtractionResult(
            document_type=doc_type,
            fields=fields,
            evidence=evidence,
            call=record,
            degraded=degraded,
            error=None if fields else "no_fields_extracted",
        )

    def _extract_common_billing(
        self, text: str, fields: dict[str, ExtractionField], doc_type: DocumentType
    ) -> None:
        amount = _AMOUNT_RE.search(text)
        if amount:
            val = _to_float(amount.group(1))
            if val is not None:
                fields["amount"] = ExtractionField(value=val, confidence=0.96)
        else:
            loose = _AMOUNT_LOOSE_RE.search(text)
            if loose:
                val = _to_float(loose.group(1))
                if val is not None:
                    fields["amount"] = ExtractionField(value=val, confidence=0.72)

        date = _DATE_RE.search(text) or _DATETIME_RE.search(text)
        if date:
            fields["date"] = ExtractionField(
                value=_normalize_date(date.group(1).split(" ")[0]), confidence=0.94
            )

        if doc_type == DocumentType.INVOICE:
            inv = _INVOICE_NO_RE.search(text)
            if inv:
                fields["invoice_no"] = ExtractionField(value=inv.group(1), confidence=0.97)
            vendor = _VENDOR_RE.search(text)
            if vendor:
                fields["vendor"] = ExtractionField(value=vendor.group(1).strip(), confidence=0.93)
            tax = _TAX_NO_RE.search(text)
            if tax:
                fields["tax_no"] = ExtractionField(value=tax.group(1), confidence=0.95)
            title = _TITLE_RE.search(text)
            if title:
                fields["title"] = ExtractionField(value=title.group(1).strip(), confidence=0.92)
            category = _CATEGORY_RE.search(text)
            if category:
                fields["category"] = ExtractionField(value=category.group(1).strip(), confidence=0.9)
            # 住宿晚数 / 参与人数（用于额度与人均计算）
            nights = _NIGHTS_RE.search(text)
            if nights:
                fields["nights"] = ExtractionField(value=int(nights.group(1)), confidence=0.92)
            attendees = _ATTENDEES_RE.search(text)
            if attendees:
                fields["attendees"] = ExtractionField(value=int(attendees.group(1)), confidence=0.9)
        else:  # receipt
            merchant = _VENDOR_RE.search(text)
            if merchant:
                fields["merchant"] = ExtractionField(value=merchant.group(1).strip(), confidence=0.93)
            pay = _PAYMENT_RE.search(text)
            if pay:
                fields["payment_method"] = ExtractionField(value=pay.group(1).strip(), confidence=0.9)

    def _extract_bank(self, text: str, fields: dict[str, ExtractionField]) -> None:
        dt = _DATETIME_RE.search(text) or _DATE_RE.search(text)
        if dt:
            raw = dt.group(1).replace("/", "-")
            fields["transaction_time"] = ExtractionField(value=raw, confidence=0.95)
        amount = _AMOUNT_RE.search(text) or _AMOUNT_LOOSE_RE.search(text)
        if amount:
            val = _to_float(amount.group(1))
            if val is not None:
                fields["amount"] = ExtractionField(value=val, confidence=0.94)
        party = _VENDOR_RE.search(text)
        if party:
            fields["counterparty"] = ExtractionField(value=party.group(1).strip(), confidence=0.92)
        txn = _TXN_NO_RE.search(text)
        if txn:
            fields["transaction_no"] = ExtractionField(value=txn.group(1), confidence=0.95)

    def _extract_approval(self, text: str, fields: dict[str, ExtractionField]) -> None:
        approver = _APPROVER_RE.search(text)
        if approver:
            fields["approver"] = ExtractionField(value=approver.group(1).strip(), confidence=0.94)
        result = _APPROVAL_RESULT_RE.search(text)
        if result:
            fields["approval_result"] = ExtractionField(value=result.group(1).strip(), confidence=0.93)
        dt = _DATE_RE.search(text) or _DATETIME_RE.search(text)
        if dt:
            fields["approved_at"] = ExtractionField(value=_normalize_date(dt.group(1).split(" ")[0]), confidence=0.9)
        reason = _APPROVAL_REASON_RE.search(text)
        if reason:
            fields["reason"] = ExtractionField(value=reason.group(1).strip(), confidence=0.91)
        category = _CATEGORY_RE.search(text)
        if category:
            fields["category"] = ExtractionField(value=category.group(1).strip(), confidence=0.89)
        trip_start = _TRIP_START_RE.search(text)
        if trip_start:
            fields["trip_start"] = ExtractionField(
                value=_normalize_date(trip_start.group(1)), confidence=0.93
            )
        trip_end = _TRIP_END_RE.search(text)
        if trip_end:
            fields["trip_end"] = ExtractionField(
                value=_normalize_date(trip_end.group(1)), confidence=0.93
            )

    @staticmethod
    def _quote_for(text: str, field_name: str, value: Any) -> str:
        """为字段生成引用原文（模拟 bbox 定位的文本侧）。"""
        needle = str(value)
        idx = text.find(needle)
        if idx < 0:
            return ""
        start = max(0, idx - 24)
        end = min(len(text), idx + len(needle) + 8)
        return text[start:end].replace("\n", " ").strip()

    # ------------------------------------------------------------------
    # Decision Agent 动作选择（确定性启发式）
    # ------------------------------------------------------------------
    async def _choose_action_impl(
        self,
        state: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> AgentAction:
        started = time.perf_counter()
        allowed = {t["name"] for t in tools}
        called = {c.get("tool_name") for c in state.get("previous_tool_calls", [])}
        failed_checks = state.get("failed_checks", []) or []
        failed_names = {c.get("rule_name") for c in failed_checks if isinstance(c, dict)}
        case = state.get("case", {}) or {}
        evidence = state.get("candidate_evidence", []) or []
        missing_docs = state.get("missing_documents", []) or []
        extracted = state.get("extracted_fields", {}) or {}
        min_conf = float(state.get("min_extraction_confidence") or 1.0)

        # 依据规则失败的严重度确定风险等级（模型应参考规则结果而非自行臆断）
        severities = {
            str(c.get("severity")) for c in failed_checks if isinstance(c, dict) and c.get("severity")
        }
        if "high" in severities:
            rule_risk = "high"
        elif "medium" in severities:
            rule_risk = "medium"
        else:
            rule_risk = "low"

        def wrap(action: AgentAction) -> AgentAction:
            action.call = ModelCallRecord(
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                input_hash=_hash_text(repr(sorted(failed_names)) + str(case.get("id", ""))),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return action

        # 1) 缺关键材料 -> 先检索制度确认「缺什么」是否真的必需，再决定 need_info
        if missing_docs and "search_policy" in allowed and "search_policy" not in called:
            return wrap(
                AgentAction.call_tool(
                    "search_policy",
                    {"query": f"{case.get('category', '')} 报销 必需材料 制度要求", "top_k": 5},
                    rationale=f"案件缺少材料 {missing_docs}，先检索制度确认材料要求",
                )
            )

        # 2) 金额不一致 -> 匹配银行流水
        if "amount_matches_bank" in failed_names and "match_bank_transaction" in allowed and "match_bank_transaction" not in called:
            return wrap(
                AgentAction.call_tool(
                    "match_bank_transaction",
                    {"amount": case.get("amount"), "tolerance": 0.01},
                    rationale="发票与流水金额不一致，重新匹配流水",
                )
            )

        # 3) 疑似重复 -> 复核历史案件
        if "duplicate_invoice" in failed_names and "check_duplicate" in allowed and "check_duplicate" not in called:
            return wrap(
                AgentAction.call_tool(
                    "check_duplicate",
                    {"invoice_no": extracted.get("invoice_no", {}).get("value") if isinstance(extracted.get("invoice_no"), dict) else extracted.get("invoice_no")},
                    rationale="疑似重复报销，复核历史案件",
                )
            )

        # 4) 超额度 / 制度冲突 -> 检索是否存在特殊审批条款
        needs_policy = bool({"category_limit", "vendor_check", "approval_required"} & failed_names)
        if needs_policy and "search_policy" in allowed and "search_policy" not in called:
            return wrap(
                AgentAction.call_tool(
                    "search_policy",
                    {"query": f"{case.get('category', '')} 额度 上限 特殊审批 例外", "top_k": 5},
                    rationale="规则失败，检索是否存在例外或特殊审批条款",
                )
            )

        # 5) 抽取置信度低 -> 重新抽取
        low_conf_conf = float(state.get("min_extraction_confidence") or 1.0)
        if low_conf_conf < 0.7 and "extract_document" in allowed and "extract_document" not in called:
            target = (state.get("low_confidence_documents") or [None])[0]
            if target:
                return wrap(
                    AgentAction.call_tool(
                        "extract_document",
                        {"document_id": target, "force": True},
                        rationale=f"字段置信度偏低（{low_conf_conf:.2f}），重新解析文档",
                    )
                )

        # 6) 缺材料且已确认制度要求 -> need_info
        if missing_docs:
            return wrap(
                AgentAction.final(
                    self._decision_payload(
                        decision="need_info",
                        risk_level=rule_risk if rule_risk in {"medium", "high"} else "medium",
                        confidence=0.82,
                        summary=f"缺少必要材料：{', '.join(missing_docs)}，请补充后重新提交。",
                        evidence=evidence,
                        failed=failed_names,
                        missing=missing_docs,
                        actions=["request_more_documents"],
                    ),
                    rationale="材料缺失，无法完成合规审核",
                )
            )

        # 7) 高风险冲突 -> escalate
        high_severity = [c for c in failed_checks if c.get("severity") == "high"]
        if high_severity:
            return wrap(
                AgentAction.final(
                    self._decision_payload(
                        decision="escalate",
                        risk_level="high",
                        confidence=0.88,
                        summary="存在高风险规则冲突：" + "；".join(
                            str(c.get("message", c.get("rule_name"))) for c in high_severity[:3]
                        ),
                        evidence=evidence,
                        failed=failed_names,
                        missing=[],
                        actions=["request_human_review"],
                    ),
                    rationale="高风险冲突必须人工审批",
                )
            )

        # 8) 全部通过且证据充足 -> approve
        # 注意：低置信度字段不得直接自动通过（文档 §7.4），因此降级为 medium 风险
        if not failed_checks and evidence:
            approve_risk = "medium" if min_conf < 0.7 else "low"
            summary = "材料完整、规则全部通过，且制度条款可引用，建议通过。"
            if approve_risk == "medium":
                summary += f"但字段抽取置信度偏低（最低 {min_conf:.2f}），需人工确认后放行。"
            return wrap(
                AgentAction.final(
                    self._decision_payload(
                        decision="approve",
                        risk_level=approve_risk,
                        confidence=round(min(0.93, 0.55 + 0.4 * min_conf), 4),
                        summary=summary,
                        evidence=evidence,
                        failed=failed_names,
                        missing=[],
                        actions=["approve"],
                    ),
                    rationale="规则通过且有制度证据支撑",
                )
            )

        # 9) 有中低风险失败但无高严重度 -> 转人工
        if failed_checks:
            return wrap(
                AgentAction.final(
                    self._decision_payload(
                        decision="escalate",
                        risk_level=rule_risk if rule_risk != "low" else "medium",
                        confidence=0.75,
                        summary="存在规则失败项，需人工复核：" + "；".join(
                            str(c.get("message", c.get("rule_name"))) for c in failed_checks[:3]
                        ),
                        evidence=evidence,
                        failed=failed_names,
                        missing=[],
                        actions=["request_human_review"],
                    ),
                    rationale="中风险失败项需人工确认",
                )
            )

        # 10) 兜底：证据不足
        return wrap(
            AgentAction.final(
                self._decision_payload(
                    decision="need_info",
                    risk_level="medium",
                    confidence=0.6,
                    summary="证据不足，无法形成可引用的审核结论，请补充材料。",
                    evidence=evidence,
                    failed=failed_names,
                    missing=["policy_evidence"],
                    actions=["request_more_documents"],
                ),
                rationale="无证据支撑结论",
            )
        )

    # ------------------------------------------------------------------
    # 噪声注入：模拟真实模型的乐观误判，用于验证 Verifier 的拦截价值
    # ------------------------------------------------------------------
    def _apply_noise(self, action: AgentAction, state: dict[str, Any]) -> AgentAction:
        """STUB_NOISE_RATE>0 时，按 (case_id, 决策) 的确定性哈希把部分非 approve
        结论改写成「无证据的乐观 approve」，且不改动本来正确的 approve。

        这样 B 组（无 Verifier）会放行错误结论，C 组（有 Verifier）应拦截，
        四组实验才会出现真实差异；噪声可复现（同一案件同一结果），不污染基线。
        """
        try:
            rate = float(os.environ.get("STUB_NOISE_RATE", "0") or 0)
        except ValueError:
            return action
        if rate <= 0 or action.type != "final":
            return action

        decision = str((action.payload or {}).get("decision", ""))
        if decision in {"", "approve"}:
            return action

        case_id = str((state.get("case") or {}).get("id", ""))
        digest = hashlib.sha256(
            f"{case_id}:{decision}:{self.prompt_version}".encode("utf-8")
        ).hexdigest()
        if (int(digest[:8], 16) % 10000) / 10000 >= rate:
            return action

        return AgentAction(
            type="final",
            payload={
                "decision": "approve",
                "risk_level": "low",
                "confidence": 0.95,
                "summary": "[stub-noise] 模型乐观误判：忽略规则失败与缺失材料，直接建议通过",
                "evidence_ids": [],
                "failed_checks": [],
                "missing_items": [],
                "proposed_actions": ["approve"],
            },
            rationale="stub_noise_injected",
            call=action.call,
        )

    async def choose_action(
        self,
        state: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> AgentAction:
        action = await self._choose_action_impl(state, tools)
        return self._apply_noise(action, state)

    @staticmethod
    def _decision_payload(
        decision: str,
        risk_level: str,
        confidence: float,
        summary: str,
        evidence: list[dict[str, Any]],
        failed: set[str | None],
        missing: list[str],
        actions: list[str],
    ) -> dict[str, Any]:
        return {
            "decision": decision,
            "risk_level": risk_level,
            "confidence": round(confidence, 4),
            "summary": summary,
            "evidence_ids": [e.get("evidence_id") for e in evidence if e.get("evidence_id")][:8],
            "failed_checks": sorted(x for x in failed if x),
            "missing_items": missing,
            "proposed_actions": actions,
        }

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        started = time.perf_counter()
        decision = request.decision or {}
        failed_checks: list[str] = []
        reasons: list[str] = []

        # 结论必须引用证据（文档 §12.1 第二层：结论是否被证据支持）
        if not decision.get("evidence_ids"):
            failed_checks.append("evidence_coverage")
            reasons.append("结论未引用任何证据")

        # approve 必须有制度证据
        if decision.get("decision") == "approve" and not request.policy_quotes:
            failed_checks.append("policy_support")
            reasons.append("approve 结论缺少制度条款引用")

        # 自相矛盾：approve 却带 high 风险或带失败项
        if decision.get("decision") == "approve" and (
            decision.get("risk_level") == "high" or request.failed_checks
        ):
            failed_checks.append("self_contradiction")
            reasons.append("approve 结论与风险等级/失败项矛盾")

        # 缺失说明：need_info 必须给出 missing_items
        if decision.get("decision") == "need_info" and not decision.get("missing_items"):
            failed_checks.append("missing_items_unspecified")
            reasons.append("need_info 结论未说明缺少的材料")

        result = VerificationResult(
            status="failed" if failed_checks else "passed",
            reason="；".join(reasons) if reasons else "结论与证据一致",
            blocking=bool(failed_checks),
            failed_checks=failed_checks,
            suggested_action="retry_agent_once" if failed_checks else "none",
            call=ModelCallRecord(
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                input_hash=_hash_text(repr(sorted(decision.keys()))),
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
        )
        return result

    # ------------------------------------------------------------------
    # 向量化与重排（确定性实现）
    # ------------------------------------------------------------------
    embedding_dim = 256

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_embedding(t) for t in texts]

    def _hash_embedding(self, text: str) -> list[float]:
        """基于字符 n-gram 的 hashing 向量（确定性、无外部依赖）。

        语义能力弱于真实 embedding，但保留了「词汇重合 → 向量相近」的基本性质，
        足以支撑混合检索链路的端到端验证与评测基线。
        """
        vec = [0.0] * self.embedding_dim
        tokens = self._tokenize(text)
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.embedding_dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    async def rerank(self, query: str, candidates: list[str], top_k: int = 5) -> list[tuple[int, float]]:
        q_tokens = set(self._tokenize(query))
        scored: list[tuple[int, float]] = []
        for i, cand in enumerate(candidates):
            c_tokens = set(self._tokenize(cand))
            if not q_tokens or not c_tokens:
                scored.append((i, 0.0))
                continue
            inter = len(q_tokens & c_tokens)
            # 以 query 覆盖率为主（检索场景更关注「是否回答了问题」）
            coverage = inter / len(q_tokens)
            jaccard = inter / len(q_tokens | c_tokens)
            scored.append((i, round(0.75 * coverage + 0.25 * jaccard, 6)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_k]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中英文混合切分：英文按词，中文按 2-gram。"""
        text = (text or "").lower()
        latin = re.findall(r"[a-z0-9_]+", text)
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
        bigrams = [
            cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)
        ]
        singles = cjk_chars
        return latin + bigrams + singles

    async def health(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "ok": True,
            "note": "stub gateway: deterministic heuristics, no external model service",
        }
