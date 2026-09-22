# -*- coding: utf-8 -*-
"""错误类型、脱敏和 key 读取——除了协议适配之外，core/ 里共用的那点管道。

原来这些跟 Jev 判断客户端挤在 core/jev_client.py 里；判断合进了起草那一次调用之后
客户端没了，这几样东西还在用，搬到这儿。JevError 沿用原名（它是整个应用的错误类型，
不是某个模型的），换了名字 llm/draft/tools 那边没别的好处。

key 只从环境变量读，任何消息出去之前都过一遍 redact_secrets，绝不把 key 打进日志。
读哪个变量跟**选中的来源**绑死（_api_key），遮哪些变量则故意放宽（providers.REDACT_ENV）。
"""

from __future__ import annotations

import os
from typing import NoReturn

try:  # 当模块导入 / 当脚本直接跑 都能用
    from .providers import DRAFT_PROVIDERS, LLM_ENV, REDACT_ENV
except ImportError:
    from providers import DRAFT_PROVIDERS, LLM_ENV, REDACT_ENV


class JevError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def redact_secrets(text: str) -> str:
    """Strip every live key from any string before print or disk write."""
    if not isinstance(text, str):
        text = str(text)
    for env in REDACT_ENV:
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


def _api_key(env: str = LLM_ENV, provider: str = "") -> str:
    """要调 provider 这家接口时用哪把 key：先 LLM_API_KEY，空着再退回**这家自己**的惯用变量。

    回退只认选中的那一家（providers 表里的 env 字段）。之前这里退回的是一个固定的老名字，
    跟选了谁无关——只导出过 DEEPSEEK_API_KEY 的人选了 OpenRouter，就会把 DeepSeek 的 key
    发给 OpenRouter。key 发错了地方，比报「没配 key」严重得多：前者神不知鬼不觉，
    后者用户看一眼就知道要干什么。

    provider 不传（或不认识）就只看 env，不回退——宁可报没配，也不替调用方猜是哪一家。
    """
    fallback = getattr(DRAFT_PROVIDERS.get(provider), "env", "")
    key = (os.environ.get(env) or "").strip()
    if not key and fallback:
        key = (os.environ.get(fallback) or "").strip()
    if not key:
        # 报错也得把第二个能用的名字说出来：那正是用户手上多半已经有的那个
        also = f" ({fallback} also works for this provider)" if fallback else ""
        raise JevError(
            f"{env} is not set{also}. Export it in the environment; "
            "do not put the key in a file."
        )
    return key


if __name__ == "__main__":
    # ponytail: 不联网。脱敏和 key 的来源绑定是这里唯二会坏的东西。
    for _name in REDACT_ENV:
        os.environ.pop(_name, None)

    # 1. 选中这家 → 用这家的惯用变量（老用户不用重填）
    os.environ["DEEPSEEK_API_KEY"] = "sk-deepseek"
    assert _api_key(LLM_ENV, "deepseek") == "sk-deepseek"
    os.environ["OPENROUTER_API_KEY"] = "sk-openrouter"
    assert _api_key(LLM_ENV, "openrouter") == "sk-openrouter"

    # 2. 别家的变量绝不当回退：选 OpenRouter 时手上只有 DeepSeek 的 key，就是没配。
    #    （这条是这个模块的全部意义：拿错 key 去调接口，比报没配 key 严重得多。）
    del os.environ["OPENROUTER_API_KEY"]
    try:
        _api_key(LLM_ENV, "openrouter")
        raise SystemExit("应当抛错：DeepSeek 的 key 不能拿去调 OpenRouter")
    except JevError as e:
        assert "sk-deepseek" not in str(e)
    # 没有惯用名的来源（moonshot / 自定义）同样不许借别人的
    try:
        _api_key(LLM_ENV, "moonshot")
        raise SystemExit("应当抛错：moonshot 没有惯用变量，不能借 DEEPSEEK_API_KEY")
    except JevError:
        pass
    # provider 不传 / 名字不认识：只看 LLM_API_KEY，不猜是哪一家
    for _bad in ("", "no_such_provider"):
        try:
            _api_key(LLM_ENV, _bad)
            raise SystemExit("应当抛错：没指明来源就不回退")
        except JevError:
            pass

    # 3. LLM_API_KEY 比两者都优先（同一把 key 走遍所有来源，换来源不用重填）
    os.environ[LLM_ENV] = "sk-new"
    os.environ["OPENROUTER_API_KEY"] = "sk-openrouter"
    assert _api_key(LLM_ENV, "deepseek") == "sk-new"
    assert _api_key(LLM_ENV, "openrouter") == "sk-new"
    assert _api_key(LLM_ENV) == "sk-new"
    del os.environ[LLM_ENV]

    # 4. 报错得告诉用户填哪个：有惯用名就两个都说（第二个正是他手上多半已经有的）
    for _name in REDACT_ENV:
        os.environ.pop(_name, None)
    try:
        _api_key(LLM_ENV, "openrouter")
        raise SystemExit("应当抛错")
    except JevError as e:
        assert LLM_ENV in str(e) and "OPENROUTER_API_KEY" in str(e), str(e)
    try:
        _api_key(LLM_ENV, "moonshot")   # 这家没有惯用名：只说 LLM_API_KEY，不编第二个
        raise SystemExit("应当抛错")
    except JevError as e:
        assert LLM_ENV in str(e) and "API_KEY" not in str(e).replace(LLM_ENV, ""), str(e)

    # 5. 遮蔽比解析宽：这一轮根本不会去读的变量，值照样得被遮掉
    os.environ[LLM_ENV] = "sk-new"
    os.environ["DEEPSEEK_API_KEY"] = "sk-old"
    os.environ["OPENROUTER_API_KEY"] = "sk-or"     # 选的是 deepseek，这一把不会被读
    assert _api_key(LLM_ENV, "deepseek") == "sk-new"
    assert redact_secrets("key=sk-new sk-old sk-or") == "key=[REDACTED] [REDACTED] [REDACTED]"
    del os.environ["OPENROUTER_API_KEY"]

    class _Boom(Exception):
        status_code = 401

    try:
        _fail(_Boom("bad key sk-new"), "起草")
        raise SystemExit("应当抛错")
    except JevError as e:
        assert e.status == 401 and "密钥被拒" in str(e) and "sk-new" not in str(e)
    print("errors ok")
