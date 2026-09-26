# 上游同步记录

这个仓库是三个上游应用各自的派生，每个变体都是「某一个上游 commit 的整棵树 + 我们那一处合并」。
**同步的前提是知道每棵树当前对齐到上游的哪个 commit**，所以每次同步都往下面这张表添一行。
2026-09-22 建仓时没记，第一次同步得靠逐个 commit 比对整棵树才找回来，别再重来一次。

| 变体 | 上游仓库 | 当前对齐到 | 上游当时的 tag | 同步日期 |
|---|---|---|---|---|
| `windows/` | [jev-chat-windows](https://github.com/jev-chat/jev-chat-windows) | `946d3d1` | `v0.1.11` | 2026-09-26 |
| `android/` | [jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis) | `71ee797` | `v1.4` + 23 | 2026-09-26 |
| `macos/` | [jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac) | `2387d71` | `v0.6.0` + 19 | 2026-09-26 |

建仓时的 fork 点（表里第一行的上一行）：`windows/` = `016eac1`、`android/` = `7c07d9d`、
`macos/` = `a28cb9e`，都在 2026-09-22。

## 怎么同步

三方合并，让 git 干机械的那部分——不要手抄 diff。每个变体一次：

```bash
git clone https://github.com/jev-chat/<repo>.git /tmp/up          # 完整历史，不要 --depth 1
git -C /tmp/up branch nojev <上表的「当前对齐到」>                  # 合并基
git -C /tmp/up worktree add /tmp/m nojev
# 把我们这棵树整个铺到 worktree 上（保留 .git，上游自己的 .github 先留着，最后再删）
# 然后提交成一个「ours」commit，再 merge 上游 HEAD
git -C /tmp/m commit -am 'chat-nojev <变体>'
git -C /tmp/m merge <上游 HEAD>
```

于是 `git diff <合并基> HEAD` 就是**我们的偏离**，`git diff <合并基> <上游 HEAD>` 就是
**上游这段时间干的事**，每个冲突 hunk 都能对着这两份判断该要哪边。解完冲突再铺回
`<变体>/`。`.github/` 的规矩：我们的 CI 在仓库根，变体目录里的 `.github/` GitHub 不会读；
`windows/`、`android/` 不带，`macos/` 只带 `ISSUE_TEMPLATE/` 和 `PULL_REQUEST_TEMPLATE.md`
（它的 `ci/` 脚本和测试引用它们），不带 workflows。

## 解冲突的规矩

1. **默认要上游的。** 先问「这个 hunk 跟『判断由谁产出』有关系吗」——无关就原样取上游，
   不顺手优化、不加注释。
2. **我们只保留 [`MERGE.md`](MERGE.md) 里记着的那些偏离**，一条不多。
3. **上游的新行为，只要不是「判断由谁产出」，都要落到合并后那条路上。** 最容易漏的一条：
   上游修了 bug、加了来源、改了提示词，哪怕它落在我们改过的函数里，也要跟上。
4. 同步完跑那五套（两个 `offline_check.py` + 三套差分 + Android 的两个负控），
   然后把这份表和 `MERGE.md` 一起更新。上游改了题面、选项集合或档位的话，根 README 里
   「题面一个字没改」那句得跟着改。

## 2026-09-26 那次同步踩过的坑

下次同步照着查一遍，这几类都不会在冲突里自己冒出来。

- **同名不同义的符号。** 上游 `033d6ef` 在 `windows/core/questions.py` 加了 `CHOICE_LABELS`
  （英文 key → 中文显示），我们那里原本也有一个 `CHOICE_LABELS`（每道题的选项 key 元组）。
  git 把两个都合进来了，后一个悄悄盖掉前一个，不报冲突。我们的已经改名 `CHOICE_OPTIONS`；
  以后自己加模块级名字前，先在上游那棵树里 grep 一下。
- **原版的兜底只见过候选。** 三处原版都有「JSON 解析不出来就逐行抠」的兜底。合并之后同一段回答
  里还有判断，`"replies": []` 会让兜底把 JSON 自己的行当候选抠出来。三套差分各有一个场景盯着
  （`all_candidates_filtered_out` / `one_tone_writes_nothing_usable`），同步完它们必须是绿的。
- **上游吞异常，桩坏了看起来像差分。** macOS 的 `_rank_payload` 把任何异常都吞成「没分」，上游
  v0.6.0 开始从 `self` 上读 `_reply_worker` / `_active_context`，桩里没有，于是 16 个场景全部
  报「我们这边分数不对」。`tools/equivalence_check_macos.py` 现在先检查上游那侧真的排出了分，
  没排出来就直接报「桩坏了」。上游再往 `self` 上加读的东西，报的是这句，按它补桩。
- **上游的新功能会改我们偏离的前提。** 这次两处：Windows 改成三段式，于是「候选盲写」这条论证
  过期、JSON 字段顺序得跟着改；macOS 每话术候选数变成可配 1–5，于是固定的输出预算不够了。
  读上游日志时，凡是碰到判断、起草、排序、候选数、提示词的提交，都要回头看 `MERGE.md` 里哪句话
  靠它成立。
- **App 里指向原版的出口。** 上游会往 App 里加链接（这次是 Android 的隐私政策和开源仓库按钮），
  默认指着它自己的站点。同步完 `grep -rnE 'chatjevs\.com|jev-jarvis\.com|github\.com/jev-chat'`
  一遍变体的源码目录，App 里能点到的都要指本仓库。

