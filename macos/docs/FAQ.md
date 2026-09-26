# 常见问题解答（FAQ）

路径与常见报错的速查。完整配置说明见 README[「配置」](../README.md#配置)，磁盘清理见[「磁盘占用与清理」](../README.md#磁盘占用与清理)。

## 配置文件在哪？

`~/.config/jev-jarvis/env`（env 格式，本项目只有这一种配置格式，没有 config.json）

```bash
cat ~/.config/jev-jarvis/env
```

⚠️ **这个文件里有你的 API key，把内容贴到 issue 或群里之前，先把 key 打码。**

## 日志在哪？

`~/Library/Logs/jev-jarvis.log`

```bash
tail -f ~/Library/Logs/jev-jarvis.log    # 实时滚动
tail -40 ~/Library/Logs/jev-jarvis.log   # 最近 40 行，贴 issue 用这个
```

日志刻意**不含消息正文与候选回复文字**，可以放心整段贴进 issue；反馈时说明当时在做什么（启动 / 首条消息 / 填入…）更好定位。

## 本地判断模型在哪？

**这一版没有本地判断模型。** 意图、风险和候选回复来自同一次模型调用（见 [README 顶部](../README.md)），
所以不下载任何权重、不占磁盘、也没有「模型设置 →『判断 · Jev』页」那一节。

上游（jev-chat-jarvis-mac）会把 `Mapika/decider-2b` 下到
`~/.cache/huggingface/hub/models--Mapika--decider-2b`，实测约 3.8 GB；这一版那个目录根本不会出现。

## 安装时提示「已损坏，无法打开，你应该将它移到废纸篓」？

浏览器下载的 zip 常见（Gatekeeper 隔离属性），右键打开也绕不过，**别删**——终端清掉隔离属性即可：

```bash
sudo xattr -r -d com.apple.quarantine /Applications/jev-jarvis.app
```

`.app` 改过名（如「jev-jarvis 2.app」）就把命令里的路径换成实际名字。装好后首次启动还需授予「屏幕录制」与「辅助功能」权限，详见 README[「只想用」](../README.md#只想用)一节。

## 启动后悬浮窗一片空白、没有任何提示？

上游那边这是**旧版本**的现象（新版会弹判断方式引导、显示模型下载进度）。这一版没有本地模型、
没有引导、也没有下载进度：面板空白的原因只剩两种——**一把 key 都没配**（启动时会弹一次提醒，
日志第一行写「未配置 Key」），或者聊天应用不在前台。先看日志：
`tail -40 ~/Library/Logs/jev-jarvis.log`。

## 还有问题？

先看 README[「已知限制」](../README.md#已知限制)与[置顶 issue](../../issues)；带上下文日志（打码后）开新 issue。数据收集与隐私说明：[PRIVACY.md](../PRIVACY.md)。
