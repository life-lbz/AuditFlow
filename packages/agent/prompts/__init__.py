"""提示词模板（文档 §10.6、附录 B、§8.5 注入防护）。"""

from .system import (
    DECISION_SYSTEM_PROMPT,
    PROMPT_VERSION,
    TOOL_CHOICE_HINT,
    UNTRUSTED_DATA_NOTICE,
)

__all__ = [
    "DECISION_SYSTEM_PROMPT",
    "PROMPT_VERSION",
    "TOOL_CHOICE_HINT",
    "UNTRUSTED_DATA_NOTICE",
]
