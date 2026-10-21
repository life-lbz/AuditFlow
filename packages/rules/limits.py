"""从制度文本中提取结构化额度（文档 §9.3：金额与额度必须由代码计算）。

制度文本由 data 生成器以固定表述产出，这里用确定性正则解析，
避免把额度判断交给模型（模型只负责检索命中，不负责算数）。
"""

from __future__ import annotations

import re
from typing import Any

_NUM = r"([0-9][0-9,]*(?:\.[0-9]+)?)"

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "hotel_nightly": (
        re.compile(rf"住宿[^。；\n]{{0,20}}?每晚(?:上限|不超过|不得超过|标准为)\s*{_NUM}\s*元"),
        re.compile(rf"酒店[^。；\n]{{0,20}}?每晚(?:上限|不超过|不得超过|标准为)\s*{_NUM}\s*元"),
    ),
    "meal_per_person": (
        re.compile(rf"(?:餐饮|用餐|伙食)[^。；\n]{{0,20}}?人均(?:上限|不超过|不得超过|标准为)\s*{_NUM}\s*元"),
        re.compile(rf"人均(?:上限|不超过|不得超过|标准为)\s*{_NUM}\s*元"),
    ),
    "office_single_item": (
        re.compile(rf"(?:办公用品)?[^。；\n]{{0,20}}?单件(?:上限|不超过|不得超过|标准为)\s*{_NUM}\s*元"),
    ),
    "travel_daily": (
        re.compile(rf"(?:差旅|出差)[^。；\n]{{0,20}}?每日(?:上限|不超过|不得超过|标准为)\s*{_NUM}\s*元"),
        re.compile(rf"每日(?:上限|不超过|不得超过|标准为)\s*{_NUM}\s*元"),
    ),
    "approval_threshold": (
        re.compile(rf"(?:单笔|单次|金额)[^。；\n]{{0,18}}?(?:超过|达到|大于|满)\s*{_NUM}\s*元[^。；\n]{{0,12}}?审批"),
        re.compile(rf"(?:超过|达到|大于|满)\s*{_NUM}\s*元[^。；\n]{{0,12}}?(?:需|须|应当)[^。；\n]{{0,8}}?审批"),
    ),
}

_CATEGORY_CAP_PATTERNS = (
    re.compile(rf"差旅[^。；\n]{{0,20}}?(?:单次|每次)?(?:上限|不超过|不得超过|总计不超过)\s*{_NUM}\s*元"),
    re.compile(rf"餐饮[^。；\n]{{0,20}}?(?:单次|每次)?(?:上限|不超过|不得超过|总计不超过)\s*{_NUM}\s*元"),
    re.compile(rf"办公用品[^。；\n]{{0,20}}?(?:单次|每次)?(?:上限|不超过|不得超过|总计不超过)\s*{_NUM}\s*元"),
)


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def extract_limits(chunks: list[dict[str, Any]]) -> dict[str, float]:
    """从检索到的制度片段中提取额度。

    chunks 元素需包含 content（原文），可含 category。后命中的值覆盖先命中的值，
    以「更具体、更靠后的条款」为准（制度通常把特殊规定写在后面）。
    """
    limits: dict[str, float] = {}
    for chunk in chunks:
        content = str(chunk.get("content") or "")
        for key, patterns in _PATTERNS.items():
            for pattern in patterns:
                m = pattern.search(content)
                if m:
                    limits[key] = _num(m.group(1))
                    break
    return limits


def extract_category_cap(chunks: list[dict[str, Any]], category: str) -> float | None:
    """提取指定报销类型的总额上限。

    优先采用含「单次/每次」的明确上限表述，避免与「每日上限」等日限额混淆。
    """
    label = {"travel": "差旅", "meal": "餐饮", "office_supplies": "办公用品"}.get(category)
    if not label:
        return None

    specific: float | None = None
    fallback: float | None = None
    for chunk in chunks:
        content = str(chunk.get("content") or "")
        if label not in content:
            continue
        for pattern in _CATEGORY_CAP_PATTERNS:
            for m in pattern.finditer(content):
                text = m.group(0)
                if label not in text:
                    continue
                value = _num(m.group(1))
                if "单次" in text or "每次" in text:
                    specific = value
                else:
                    fallback = value
    return specific if specific is not None else fallback
