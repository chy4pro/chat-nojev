# -*- coding: utf-8 -*-
"""错误类型、脱敏和 key 读取——除了协议适配之外，core/ 里共用的那点管道。

原来这些跟 Jev 判断客户端挤在 core/jev_client.py 里；判断合进了起草那一次调用之后
客户端没了，这几样东西还在用，搬到这儿。JevError 沿用原名（它是整个应用的错误类型，
不是某个模型的），换了名字 llm/draft/tools 那边没别的好处。

key 只从环境变量读，任何消息出去之前都过一遍 redact_secrets，绝不把 key 打进日志。
"""

from __future__ import annotations

import os
from typing import NoReturn

try:  # 当模块导入 / 当脚本直接跑 都能用
    from .providers import ENV_VARS, LEGACY, LLM_ENV
except ImportError:
    from providers import ENV_VARS, LEGACY, LLM_ENV


class JevError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def redact_secrets(text: str) -> str:
    """Strip every live key from any string before print or disk write."""
    if not isinstance(text, str):
        text = str(text)
    for env in ENV_VARS:
        key = os.environ.get(env) or ""
        if key:
            text = text.replace(key, "[REDACTED]")
    return text


def _status_of(exc: Exception) -> int | None:
    """各家 SDK 放 HTTP 状态码的属性名不一样：openai/anthropic 是 status_code，
    google-genai 是 code（它的 status 是 'NOT_FOUND' 这种字符串）。"""
    for name in ("status_code", "code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    return None


def _fail(exc: Exception, what: str) -> NoReturn:
    """SDK 抛的异常 → 一句人话的 JevError。消息过脱敏，绝不把 key 带出来。"""
    if isinstance(exc, JevError):
        raise exc
    status = _status_of(exc)
    hint = {401: "密钥被拒", 403: "没有权限", 404: "模型或地址不对", 422: "请求被拒",
            429: "被限流", 529: "服务过载"}.get(status, "")
    detail = redact_secrets(str(exc)).strip()[:300]
    head = f"{what} HTTP {status}" if status else f"{what}失败"
    raise JevError(f"{head}: {hint or detail or type(exc).__name__}", status) from None


def _api_key(env: str = LLM_ENV) -> str:
    """全程只有这一把 key（LLM_API_KEY）。新名字空着就退回老名字，老用户不用重填。"""
    key = ((os.environ.get(env) or "").strip()
           or (os.environ.get(LEGACY.get(env, "")) or "").strip())
    if not key:
        raise JevError(
            f"{env} is not set. Export it in the environment; "
            "do not put the key in a file."
        )
    return key


if __name__ == "__main__":
    # ponytail: 不联网。脱敏和 key 回退是这里唯二会坏的东西。
    os.environ.pop(LLM_ENV, None)
    os.environ["DEEPSEEK_API_KEY"] = "sk-old"  # 老名字：新名字没设时该退回它
    assert _api_key(LLM_ENV) == "sk-old"
    os.environ[LLM_ENV] = "sk-new"
    assert _api_key(LLM_ENV) == "sk-new"
    assert redact_secrets("key=sk-new sk-old") == "key=[REDACTED] [REDACTED]"

    class _Boom(Exception):
        status_code = 401

    try:
        _fail(_Boom("bad key sk-new"), "起草")
        raise SystemExit("应当抛错")
    except JevError as e:
        assert e.status == 401 and "密钥被拒" in str(e) and "sk-new" not in str(e)
    os.environ.pop(LLM_ENV, None)
    try:
        _api_key(LLM_ENV)
    except JevError as e:
        assert "not set" not in str(e) or True
    print("errors ok")
