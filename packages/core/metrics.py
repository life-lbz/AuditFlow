"""进程内指标采集与 Prometheus 文本渲染。

不引入 prometheus-client，避免为一个端点增加运行时依赖；指标以进程内累计值暴露，
多副本部署时应改为 Pushgateway 或由 Prometheus 抓取每个副本后汇总。
"""

from __future__ import annotations

import threading
from collections import defaultdict


class _Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency: dict[tuple[str, str], tuple[int, float]] = {}

    def observe(self, method: str, path: str, status: int, elapsed_ms: float) -> None:
        with self._lock:
            self._requests[(method, path, int(status))] += 1
            count, total = self._latency.get((method, path), (0, 0.0))
            self._latency[(method, path)] = (count + 1, total + elapsed_ms)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "requests": dict(self._requests),
                "latency": dict(self._latency),
            }

    def reset(self) -> None:
        """测试用：清空累计指标。"""
        with self._lock:
            self._requests.clear()
            self._latency.clear()


registry = _Registry()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(*, extra: list[str] | None = None) -> str:
    """渲染 Prometheus text exposition 格式。"""
    snap = registry.snapshot()
    lines: list[str] = []
    lines.append("# HELP auditflow_http_requests_total HTTP 请求总数")
    lines.append("# TYPE auditflow_http_requests_total counter")
    for (method, path, status), count in sorted(snap["requests"].items()):
        lines.append(
            f'auditflow_http_requests_total{{method="{method}",path="{_escape(path)}",status="{status}"}} {count}'
        )
    lines.append("# HELP auditflow_http_request_duration_ms_sum 累计请求耗时（毫秒）")
    lines.append("# TYPE auditflow_http_request_duration_ms_sum counter")
    lines.append("# HELP auditflow_http_request_duration_ms_count 请求次数")
    lines.append("# TYPE auditflow_http_request_duration_ms_count counter")
    for (method, path), (count, total) in sorted(snap["latency"].items()):
        label = f'method="{method}",path="{_escape(path)}"'
        lines.append(f"auditflow_http_request_duration_ms_sum{{{label}}} {total:.1f}")
        lines.append(f"auditflow_http_request_duration_ms_count{{{label}}} {count}")
    if extra:
        lines.extend(extra)
    return "\n".join(lines) + "\n"
