# -*- coding: utf-8 -*-
"""来源表：语言模型。纯数据，不联网、不认 key。

表里只有协议、地址和默认模型，**绝不出现 key**（KICKOFF 硬约束 #6）——
key 一律由调用方从环境变量/注册表取了再传进来。协议具体怎么调见 core/llm.py。

全程只有一把 key：LLM_API_KEY，跟选哪家来源无关，换来源就是换同一个槽里的值。
（上游还有一把判断模型的 JEV_API_KEY；判断合进了起草那一次调用，那把 key 没有了。）

它空着的时候允许退回「这家自己的惯用变量」（下表的 env 字段），**只认选中的那一家**：
用户手上多半已经为某几家导出过 OPENROUTER_API_KEY / DEEPSEEK_API_KEY 这种名字，选中谁就用
谁的，省一次重填；但绝不能拿 A 家的 key 去调 B 家的接口——那不是「凑合能用」，是把密钥发给了
不该拿到它的一方。所以惯用名必须长在来源条目旁边，跟着来源一起被选中，不能是一张谁都能命中的表。
"""
from __future__ import annotations

from collections import namedtuple

OPENROUTER_BASE = "https://openrouter.ai/api/v1"  # OpenAI 兼容，列模型走它

LLM_ENV = "LLM_API_KEY"    # 唯一一把，不管选哪家语言模型

# protocol ∈ {openai, anthropic, gemini}：决定 core/llm.py 用哪个官方 SDK
# base 空 = 用 SDK 自带的默认地址（gemini），或者等用户自己填（自定义来源）
# default 空 = 这家没有钦点的默认模型，用户得「获取模型」自己挑一个
# extra：OpenAI 协议下开/关思考模式要额外带的 body 字段，各家不一样；
#        anthropic / gemini 的思考开关是协议自带的参数，由 llm.py 直接处理，这里给空
# env：这家在生态里的惯用密钥变量名。LLM_API_KEY 空着时，**只有选中这家**才会退回读它
#      （core/errors.py 的 _api_key）。没有公认惯例的（moonshot / zhipu / dashscope /
#      siliconflow 和两个自定义来源）一律留空：宁可让用户填 LLM_API_KEY，也不替他们编一个
#      名字去猜——猜错了就是拿别人的 key 去调这家的接口。
_Draft = namedtuple("_Draft", "name protocol base default extra env", defaults=("",))
_NONE = lambda on: {}  # noqa: E731 —— 没有思考开关的来源
DRAFT_PROVIDERS = {  # 第一个就是默认：DeepSeek 官网直连
    "deepseek": _Draft("DeepSeek 官网", "openai", "https://api.deepseek.com", "deepseek-flash",
                       lambda on: {"thinking": {"type": "enabled" if on else "disabled"}},
                       "DEEPSEEK_API_KEY"),
    "openrouter": _Draft("OpenRouter", "openai", OPENROUTER_BASE,
                         "deepseek/deepseek-v4.1-flash", lambda on: {"reasoning": {"enabled": on}},
                         "OPENROUTER_API_KEY"),
    "openai": _Draft("OpenAI", "openai", "https://api.openai.com/v1", "", _NONE,
                     "OPENAI_API_KEY"),
    "moonshot": _Draft("Moonshot (Kimi)", "openai", "https://api.moonshot.cn/v1", "", _NONE),
    "zhipu": _Draft("智谱 GLM", "openai", "https://open.bigmodel.cn/api/paas/v4", "", _NONE),
    "dashscope": _Draft("通义千问", "openai",
                        "https://dashscope.aliyuncs.com/compatible-mode/v1", "", _NONE),
    "siliconflow": _Draft("硅基流动", "openai", "https://api.siliconflow.cn/v1", "", _NONE),
    "anthropic": _Draft("Anthropic", "anthropic", "https://api.anthropic.com", "", _NONE,
                        "ANTHROPIC_API_KEY"),
    "gemini": _Draft("Google Gemini", "gemini", "", "", _NONE, "GEMINI_API_KEY"),
    "custom_openai": _Draft("自定义 · OpenAI 兼容", "openai", "", "", _NONE),
    "custom_anthropic": _Draft("自定义 · Anthropic 兼容", "anthropic", "", "", _NONE),
}

# 历史遗留：v1 只有 DeepSeek 一家，LLM_API_KEY 的「老名字」就是它的惯用名。回退现在由上面
# 那张表按**选中的来源**决定，这张表只剩兼容引用（不参与解析）。别往里加第二条——加了就又
# 变回「不管选哪家都退回它」的那个毛病。
LEGACY = {LLM_ENV: DRAFT_PROVIDERS["deepseek"].env}

# 这两个来源没有固定地址，设置页要多露一行 Base URL 出来
CUSTOM = ("custom_openai", "custom_anthropic")
# 起草时认思考开关的来源，设置页那句提示照着这里写
THINKING = ("DeepSeek", "OpenRouter", "Anthropic", "Gemini")
# 脱敏清单：所有**可能出现在这台机器上**的 key 变量名，一次全过一遍（errors.redact_secrets）。
# 故意比解析宽：解析一次最多看两个名字（LLM_API_KEY + 选中那家的 env），遮蔽要把每一个
# 可能有值的名字都遮上——包括这一轮根本不会去读的那些。OPENROUTER_API_KEY 就是典型：
# 从 jev-chat-windows 升上来的机器上多半还留着它，选的却是 DeepSeek，这一轮不读它，可它
# 一旦被别的东西带进错误字符串里，还是得遮住。
# 两张清单**不能合并**：宽的那张合进解析，就等于「谁的 key 都能拿去调谁的接口」；
# 窄的那张当成遮蔽，就等于把没读过的 key 原样打进日志。方向相反，各留各的。
REDACT_ENV = sorted({LLM_ENV, *LEGACY.values(),
                     *(p.env for p in DRAFT_PROVIDERS.values() if p.env)})
ENV_VARS = sorted({LLM_ENV, *LEGACY.values()})  # 设置页会写的那几个（只有 LLM_API_KEY 会被写）


if __name__ == "__main__":
    # ponytail: 纯数据，只查几条不变式——协议打错字、自定义来源漏配 Base URL、思考字段写反最容易出。
    assert {p.protocol for p in DRAFT_PROVIDERS.values()} == {"openai", "anthropic", "gemini"}
    assert all(p.base or key in CUSTOM or p.protocol == "gemini"
               for key, p in DRAFT_PROVIDERS.items())
    assert all(not DRAFT_PROVIDERS[key].base for key in CUSTOM)
    assert next(iter(DRAFT_PROVIDERS)) == "deepseek"  # 默认就是列表第一个
    assert DRAFT_PROVIDERS["deepseek"].extra(True) == {"thinking": {"type": "enabled"}}
    assert DRAFT_PROVIDERS["deepseek"].extra(False) == {"thinking": {"type": "disabled"}}
    assert DRAFT_PROVIDERS["openrouter"].extra(True) == {"reasoning": {"enabled": True}}
    assert DRAFT_PROVIDERS["moonshot"].extra(True) == {}
    # 全程只有一把 key，脱敏还得管老名字
    assert ENV_VARS == ["DEEPSEEK_API_KEY", "LLM_API_KEY"]
    # 惯用名长在来源旁边：有惯例的填上，没惯例的（含两个自定义来源）必须留空，不许编
    assert DRAFT_PROVIDERS["openrouter"].env == "OPENROUTER_API_KEY"
    assert DRAFT_PROVIDERS["deepseek"].env == "DEEPSEEK_API_KEY"
    assert DRAFT_PROVIDERS["gemini"].env == "GEMINI_API_KEY"
    assert all(not DRAFT_PROVIDERS[key].env for key in CUSTOM)
    assert not any(DRAFT_PROVIDERS[key].env
                   for key in ("moonshot", "zhipu", "dashscope", "siliconflow"))
    # 遮蔽比解析宽：每个来源的惯用名都得在遮蔽清单里，反过来不成立
    assert {p.env for p in DRAFT_PROVIDERS.values() if p.env} < set(REDACT_ENV)
    assert "OPENROUTER_API_KEY" in REDACT_ENV and "OPENROUTER_API_KEY" not in LEGACY.values()
    print("providers ok")
