"""日志配置：text（开发可读）与 json（生产采集）两种格式。

访问日志由 apps/api/main.py 的中间件输出，字段通过 ``extra`` 传入；
JSON 格式下这些字段会被提升为一级属性，方便 Loki / ELK 直接检索。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_EXTRA_FIELDS = (
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
    "user_id",
    "event",
    "error_code",
)

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """初始化根日志；重复调用为幂等操作，避免测试中重复挂载 handler。"""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    if (fmt or "text").strip().lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel((level or "INFO").upper())

    # uvicorn 自带 handler 且 propagate=False，这里统一接管，保证输出格式一致
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        target.handlers = [handler]
        target.propagate = False
        target.setLevel((level or "INFO").upper())

    _configured = True
