<!-- 一个 PR 只做一件事；不适用的小节可删，但「怎么测的」不能空 -->

## 改了什么

<!-- 一句话说清改动范围，例如：修改 src/perception.py 聊天区指纹的比对逻辑；按【新增】/【修改】/【删除】标注点到了哪些类、方法、配置 -->

## 为什么改

<!-- 关联 issue，例如：Closes #12；没有 issue 的新想法先开 issue 等维护者确认 -->

## 怎么测的

<!-- 手动验证方式或日志片段；有实测数字就写实测数字。CI 有两道覆盖率门禁（基线棘轮 + 改动行 ≥80%），红灯不许合 -->

- [ ] 开工前已认领：issue 下评论认领 + 设了 assignee（见 CONTRIBUTING.md 认领三步自检）
- [ ] 离线回归跑过：`uv run --locked python -B -m unittest discover -s tests`
- [ ] 覆盖率自查过（本地命令见 CONTRIBUTING「自测要求」，或直接看本 PR 的 CI 结果）
- [ ] 守住核心原则：**纯只读**——不注入、不 hook、不解密微信数据（「填入」走辅助功能接口，未改回剪贴板 + Cmd+V，原因见 `src/fill.py` 顶部注释）
- [ ] 未引入硬编码的 API Key / 密码（密钥只能放仓库外的 `~/.config/jev-jarvis/env`）
- [ ] 改了判断题面（`src/questions.py`）或合并那一次调用的解析（`src/generate.py`）→ 已重跑 `uv run python tools/offline_check.py`（未改可删）
- [ ] 改了用户可见行为 → 已更新 README.md
