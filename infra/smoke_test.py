"""部署后冒烟检查（不依赖 pytest，直接打运行中的 HTTP 服务）。

用法：
    python infra/smoke_test.py --base http://localhost:8000
    python infra/smoke_test.py --base http://localhost:8080/api   # 经 nginx 反代
    python infra/smoke_test.py --base http://localhost:8000 --token <bearer>

检查项：健康、请求追踪头、运行信息、制度清单、案件清单、指标端点。
任一失败即返回非 0，可直接作为发布流水线的最后一道门禁。
"""

from __future__ import annotations

import argparse
import sys

import httpx


def _check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}{f' - {detail}' if detail else ''}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="AuditFlow 部署冒烟")
    parser.add_argument("--base", default="http://localhost:8000", help="API 根地址")
    parser.add_argument("--token", default="", help="token 鉴权模式下的 Bearer Token")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    passed = True

    with httpx.Client(base_url=base, timeout=10.0, headers=headers) as client:
        try:
            health = client.get("/health")
            passed &= _check(
                "GET /health", health.status_code == 200, f"status={health.status_code}"
            )
            passed &= _check(
                "X-Request-ID 响应头", bool(health.headers.get("X-Request-ID"))
            )
            if health.status_code != 200:
                print("服务不可用，后续检查跳过")
                return 1
        except httpx.HTTPError as exc:
            print(f"[FAIL] 无法连接 {base} - {exc}")
            return 1

        for path in ("/system/info", "/policies", "/cases", "/eval/cases"):
            response = client.get(path)
            passed &= _check(
                f"GET {path}", response.status_code == 200, f"status={response.status_code}"
            )

        metrics = client.get("/metrics")
        ok = metrics.status_code == 200 and "auditflow_" in metrics.text
        passed &= _check("GET /metrics", ok, f"status={metrics.status_code}")

        info = client.get("/system/info")
        if info.status_code == 200:
            payload = info.json()
            passed &= _check(
                "系统信息字段完整",
                {"model_provider", "storage_backend"}.issubset(payload.keys()),
                f"keys={sorted(payload.keys())[:6]}",
            )

    print("\n冒烟结果：" + ("全部通过" if passed else "存在失败项"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
