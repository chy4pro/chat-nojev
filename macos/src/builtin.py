"""随包分发的内置默认凭据 —— 用户一个 key 都没配时用它，应用开箱就能出候选。

⚠️ 这个文件一进 git 就等于公开：任何人解开 .app 或翻仓库都能拿到这把 key。
所以这里放的必须是**专用 token**，而不是主账号 key：

    · 模型白名单：只勾分发用的那个模型（New API 的「模型限制」）
    · 额度封顶：给个上限。不限额的 token 泄露 = 别人能把你上游账户刷干
    · 过期时间：用户不用了就自动失效，不必手工吊销

轮换只改下面几行，全项目没有第二处。留空 API_KEY 就退回老行为：
用户必须自己配，否则候选区空着 + 启动弹窗。
"""

# 自建中转（One API / New API）
API_KEY = "sk-WwZDJLxyZSiLESeLjVTySpCiwjcNoJauuGVkWPNEpI2NyDbQ"
BASE_URL = "http://101.132.131.220:11111/v1"

# 中转换渠道时不用改这里：启动会问一次中转「现在提供哪些模型」，按下面顺序挑第一个
# 存在的；都挑不到就取中转返回的第一个，问不到（离线／接口不兼容）才用 MODEL 硬上。
# 写死单个名字的代价是：包一旦分发出去，每个副本都冻结了那个名字，之后换渠道全废。
MODEL = "glm-4-flash"
MODEL_PREFERENCE = ["glm-4-flash", "Qwen/Qwen3-8B"]

# 端点要靠额外字段才能关思考时填这里（格式同 env 的 OPENAI_EXTRA_BODY，用户配了以 env 为准）。
# 统一发 enable_thinking=false：Qwen3 那类默认开思考的模型不关会慢到 85 秒，而 glm-4-flash
# 实测把它当未知字段忽略、不报错——所以这一条同时兼容两种渠道，不必按模型分开处理。
EXTRA_BODY = '{"enable_thinking": false}'
