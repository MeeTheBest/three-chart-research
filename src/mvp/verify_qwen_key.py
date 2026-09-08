"""Verify Qwen Model Studio authentication without transmitting birth data."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def normalized_key() -> str:
    value = (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value.strip("'\"")


def main() -> int:
    key = normalized_key()
    if not key:
        print("未读取到通义千问（百炼）API Key。", file=sys.stderr)
        return 1
    api_url = os.environ.get("QWEN_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    model = os.environ.get("QWEN_MODEL", "qwen3.7-max-2026-06-08")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Return exactly {\"ok\":true} as JSON."}],
        "enable_thinking": True,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 32,
        "stream": False,
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise urllib.error.HTTPError(api_url, response.status, "unexpected status", response.headers, None)
    except urllib.error.HTTPError as exc:
        messages = {
            401: "百炼拒绝该 Key（HTTP 401）。请确认 API Key、地域和业务空间一致。",
            402: "百炼账户余额或套餐不可用（HTTP 402）。请检查百炼账户余额。",
            403: f"当前业务空间无权调用 {model}（HTTP 403）。请在模型广场开通该模型，或设置 QWEN_MODEL。",
            429: "百炼请求过于频繁或额度受限（HTTP 429）。请稍后重试。",
        }
        print(messages.get(exc.code, f"通义千问 Key 验证失败（HTTP {exc.code}）。"), file=sys.stderr)
        return 1
    except urllib.error.URLError:
        print("无法连接通义千问（百炼），未验证 Key。请检查网络后重试。", file=sys.stderr)
        return 1
    print(f"通义千问 Key 验证成功（{model}）；未发送任何出生信息。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
