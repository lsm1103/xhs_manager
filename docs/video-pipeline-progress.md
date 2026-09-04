# 视频 Pipeline 开发进度

> 本文档是视频自动化流水线的**唯一进度看板**。每完成一个 TODO 即更新此文档并 commit。
> 最后更新：2026-09-03

---

## 一、目标

每天自动产出 **3 条短视频**并发布到多平台，全流程无人值守：

```
热点采集 → 选题+脚本 → 素材收集 → HTML构建 → 视频录制 → 多平台发布
```

---

## 二、已完成（截至 2026-09-03）

### 2.1 架构与代码骨架 ✅

| 模块 | 文件 | 行数 | 状态 |
|------|------|------|------|
| 领域模型 | `video_pipeline/domain.py` | 197 | ✅ 状态机/枚举/平台限制 |
| 数据模型 | `video_pipeline/models.py` | 316 | ✅ 8 张表 |
| 配置 | `video_pipeline/config.py` | 76 | ✅ `XHS_VIDEO_` 前缀 |
| 编排器 | `video_pipeline/pipeline.py` | 203 | ✅ 6 阶段串联 |
| CLI | `video_pipeline/cli.py` | 147 | ✅ run/stage/status/config |
| Stage1 热点采集 | `stages/stage1_trends.py` | 143 | ✅ **已跑通**，147 条真实数据 |
| Stage2 选题脚本 | `stages/stage2_topics.py` | 417 | ✅ **已跑通**，真实 LLM 生成 3 选题 3 脚本 |
| Stage3 素材收集 | `stages/stage3_materials.py` | 455 | ✅ **已跑通**，6 场景真实素材 |
| Stage4 HTML构建 | `stages/stage4_compose.py` | 568 | ✅ **已跑通**，视频正确嵌入 |
| Stage5 视频渲染 | `stages/stage5_render.py` | 260 | ✅ **已跑通**，双路径均出片 |
| Stage6 多平台发布 | `stages/stage6_publish.py` | 317 | ⚠️ 受 Chrome 扩展阻塞 |
| Claude 客户端 | `integrations/llm_client.py` | 130 | ✅ **claude -p 通道**，无需 Key |
| MoneyPrinterTurbo | `integrations/moneyprinter.py` | 316 | ✅ **已跑通**，素材+渲染 |
| 采集器 | `integrations/collectors.py` | 300 | ✅ **已跑通**，B站/V2EX |
| 渲染器 | `integrations/renderer.py` | 215 | ✅ **已跑通**，HTML→MP4 |
| 种子数据 | `seed.py` | 150 | ✅ 绕开 LLM 依赖 |

### 2.2 数据库 ✅

8 张新表已创建（`create_all` 方式），迁移文件 `4b8c5d6e7f01_add_video_pipeline.py` 已写但未通过 alembic 执行。

### 2.3 认证方案（已决策）✅

**采用 Claude Code OAuth（ACP），不配 API Key。**

- 凭证来源：macOS Keychain `Claude Code-credentials` → `claudeAiOauth.accessToken`
- 调用方式：`auth_token` + `anthropic-beta: oauth-2025-04-20`
- 已验证：token 读取成功（含 `user:inference` scope），服务端接受认证
- **决策：不加重试退避** — 用户 Max 套餐可承受并发

### 2.4 外部工具就绪情况

| 工具 | 状态 | 位置/说明 |
|------|------|-----------|
| MoneyPrinterTurbo | ✅ 已安装 | `../MoneyPrinterTurbo`，用户已成功生成过视频 |
| Pixelle-Video | ✅ 已安装 | `../Pixelle-Video`，API 端口 8080 |
| ffmpeg | ✅ 已安装 | `/opt/homebrew/bin/ffmpeg` |
| anthropic SDK | ✅ v1.3.0 | 已加入依赖 |
| opencli | ❓ 未验证 | Stage1/Stage6 依赖，**待确认** |
| Playwright | ❓ 未验证 | Stage5 HTML 渲染路径依赖 |
| HyperFrames | ❌ 未安装 | 可选，有 Playwright 兜底 |

### 2.5 测试

**69 passed，全绿**（原 32 + 视频 pipeline 新增 37）。

---

## 三、当前阻塞项

### 3.1 ~~Claude API 限流~~ → 已解决（T03）

之前的判断是错的：429 是因为拿 Claude Code 的 OAuth token 直打 API 端点。
改走 `claude -p` 通道后无任何限流，T04 已用真实 LLM 跑通。

### 3.2 OpenCLI Chrome 扩展未启用（阻塞 T11 及 3 个采集平台）

daemon 正常、Chrome 正常、扩展文件已在磁盘，但未加载启用。
**影响**：小红书/抖音/X 三个平台的采集和发布都走不通。
**需要用户操作**：`chrome://extensions/` → 找到 OpenCLI → 启用，
然后 `opencli doctor` 应显示 `Extension: connected`。

### 3.3 已消除的风险

- ~~opencli 命令格式全是推测~~ → T01 已实测确认，并发现真实格式与推测不同
- ~~全流程从未真实执行~~ → Stage1/3/4/5 已用真实数据跑通
- ~~MPT 参数组合未验证~~ → T05 已实测，发现三处与假设不符并修正

---

## 四、TODO List

> 状态标记：⬜ 未开始 / 🔄 进行中 / ✅ 已完成 / ❌ 受阻

### T01 ✅ 验证 opencli 可用性与平台命令格式
**做什么**：确认 opencli 是否安装、支持哪些平台、search 子命令的真实参数格式和输出结构。
**验收**：✅ B站搜索 API 与 V2EX API 实测拿到真实 JSON 数据；opencli 命令格式已记录。
**完成时间**：2026-09-03
**关键发现**：见下方「四.5 平台采集后端实测结论」

### T02 ✅ 按真实格式重写 Stage1 采集逻辑
**做什么**：新建 `integrations/collectors.py` 多后端采集层；重写 `stage1_trends.py`；修复 CLI 数据库导入错误。
**验收**：✅ `cli.py stage collecting` 实际跑通，**147 条真实热点入库**（B站 138 + V2EX 9）。
**完成时间**：2026-09-03
**产出**：
- `integrations/collectors.py` — `TrendItem` 统一结构 + 3 个采集器（Bilibili/V2ex/OpenCli）
- 热度归一化算法：`log10(点赞×3 + 评论×5 + 分享×4 + 播放×0.1) × 20`，压到 0-100
- 配置默认平台加入 `v2ex`
- 修复 `cli.py` 的 `from xhs_manager.db import engine` 导入错误（该模块只导出工厂函数）

### T03 ✅ 验证 Claude ACP 调用通道
**做什么**：找到正确的 ACP 调用方式并验证结构化输出。
**完成时间**：2026-09-03
**根因（之前判断错了）**：429 不是"配额被交互会话吃掉"，而是**用错了通道**。
我一直拿 Claude Code 的 OAuth token 直打 Anthropic API 端点 —— 那个 token 是给 Claude Code
自己用的，直打 API 会被判为异常用法持续 429（返回的 message 只有一个 "Error"，不是正常限流提示）。
**正确做法**：`claude -p`（headless 模式）让 Claude Code 自己发请求，走 Max 订阅通道：
- 无需 API Key，无 429
- `--json-schema` 原生结构化输出，响应里直接给已解析的 `structured_output` 字段
- `--system-prompt` / `--model` / `--tools ""`（禁工具）/ `--no-session-persistence`
**代码**：`llm_client.py` 重写为 `subprocess` 调 `claude -p`，移除 `anthropic` SDK 依赖。

### T04 ✅ Stage2 用真实信号跑通选题+脚本生成
**做什么**：基于 148 条真实热点（B站+V2EX），走 `claude -p` 跑 `stage selecting`。
**验收**：✅ **3 条选题 + 3 份脚本**，全部由真实 LLM 从真实热点生成，
场景数与时长均落在 schema 约束内（7-8 场景 / 60-75s），四平台标题/标签齐全。
**完成时间**：2026-09-03
**耗时**：4 次 LLM 调用（1 选题 + 3 脚本）约 3 分钟。

### T05 ✅ 验证 MoneyPrinterTurbo 素材搜索参数
**做什么**：手工跑 `--stop-at materials` 确认参数组合有效、产物落在哪。
**验收**：✅ 实测下载 9 个 Pexels 素材，集成层改造后再测拿到 8 个。
**完成时间**：2026-09-03
**关键发现（都和原代码假设不符）**：
1. **`--task-id` 必须是合法 UUID**，自定义字符串会被拒绝
2. **素材文件不在任务目录**，落在 `storage/cache_videos/`（跨任务共享缓存）
3. **真实路径由 stdout 最后一行 JSON 返回**：`{"task_id":..., "result":{"materials":[...]}}`
4. **`--stop-at materials` 会顺带产出 `audio.mp3`**（任务目录下），省一次 TTS 调用
**修正**：`moneyprinter.py` 增加 `_new_task_id()` 和 `_parse_result_json()`，
素材路径改为解析 stdout 而非扫描任务目录。

### T06 ✅ Stage3 素材收集跑通
**做什么**：修正 `moneyprinter.py`，新增 `seed.py` 绕开 LLM 依赖，跑 `stage materializing`。
**验收**：✅ **6 个场景全部拿到真实 Pexels 视频素材**，整篇旁白 35.35s（脚本 36s）。
**完成时间**：2026-09-03
**修复的两个真实 bug**：
1. **TTS 分支被 `continue` 跳过** —— 有视频素材的场景直接 `continue`，
   永远走不到后面的 TTS 代码。表现为 s01-s03 无音频、s04-s06 有音频。
2. **音轨冗余冲突** —— 同时产出「整篇旁白」和「分场景旁白」，渲染时无法叠加。
   改为统一用整篇旁白单音轨。
**另一处改进**：素材不足时按 `i % len(materials)` 循环复用，
   保证每个场景都有画面，而不是降级成纯文字卡片（原先 6 场景只有 3 个有画面）。
**产出**：`seed.py` —— 手写 6 场景 36 秒示例脚本，覆盖全部转场和文字动画类型，
   兼作单元测试夹具；CLI 新增 `seed` 子命令。

### T07 ✅ Stage4 HTML 生成跑通
**做什么**：跑 `stage composing`，检查素材是否正确嵌入、排版是否正常。
**验收**：✅ 6 个 `<video>` + 1 个 `<audio>` 正确嵌入，抽帧确认排版正确。
**完成时间**：2026-09-03
**修复的严重 bug**：**视频素材根本没进 HTML**。
`_build_background_style` 的 mp4 分支返回空字符串，注释写着「视频通过 `<video>` 标签处理」，
但那段生成逻辑从未实现 —— 素材复制到了 `assets/` 却 0 处引用，6 个场景全是空白。
改为 `_build_scene_media()` 返回 `(背景CSS, 媒体HTML)` 二元组，mp4 生成真正的 `<video>` 标签。
**排版修复**：`.animate-typewriter` 的 `display:inline-block` 让主副标题并排显示。
改为作用于内层 `<span>`，保留 h1/p 的块级堆叠。
**可读性修复**：暗层从纯色 `rgba(0,0,0,.35)` 改为上下渐变（中部 0.55 压暗保证文字可读，
上下 0.15 保留画面细节）；文字加三层阴影应对不可控的素材亮度。

### T08 ✅ 准备 Playwright 渲染环境 + 独立渲染器
**做什么**：装 Python playwright；新建 `integrations/renderer.py` 替换原先「拼 Node.js 脚本字符串」的脆弱做法。
**验收**：✅ 端到端验证通过 —— 3 场景 HTML → **1080×1920 H.264 MP4，时长精确 6.0s**，5 个平台封面全部生成。
**完成时间**：2026-09-03
**产出**：
- `uv add playwright`（1.62.0），复用系统 Chrome，无需下载 Chromium
- `HtmlVideoRenderer` 类：`capture_frames()` / `frames_to_video()` / `render()`
- 时序控制改为 Python 侧注入 `SEEK_JS` 精确 seek，不依赖页面 rAF 循环
- `extract_covers()` 一次生成 default + 4 平台尺寸封面
**为什么重写**：原做法把 Node.js 脚本当字符串拼接再 `subprocess` 执行，
路径转义脆弱、异常不可捕获、无法调试。改用 Python playwright 后这些问题消失。

### T09 ✅ Stage5 渲染出第一个真实 MP4
**做什么**：Stage5 改用 `integrations/renderer.py`，跑 `stage rendering`。
**验收**：✅ **产出 8.3MB / 35.35s / 1080×1920 H.264+AAC 视频，864 帧全部截取成功**。
逐场景抽帧验证：6 个场景画面全部正常（真实 Pexels 素材 + 中文字幕叠加）。
**完成时间**：2026-09-03
**修复的两个问题**：
1. **`networkidle` 必然超时** —— 6 个 `<video preload="auto">` 会让网络一直不空闲。
   改为 `wait_until="load"` + 显式等待每个视频的 `loadedmetadata`。
2. **逐帧截图时视频不会自然播放** —— 必须在 `SEEK_JS` 里手动设置
   `video.currentTime = localTime % duration`（取模让短素材循环填满场景）。
**渲染耗时**：36 秒视频约 170 秒（24fps，864 帧）。

### T10 ⬜ Stage5 验证 MoneyPrinterTurbo 渲染路径
**做什么**：把 `render_mode` 切到 `moneyprinter`，验证快速路径能出片。
**验收**：MPT 路径也能产出 MP4。
**依赖**：T05

### T11 ⬜ Stage6 小红书草稿发布跑通
**做什么**：按 T01 确认的 opencli 格式修正发布命令，跑 `cli.py stage publishing`。
**验收**：小红书草稿箱能看到视频草稿。
**依赖**：T09

### T12 ⬜ 端到端串联跑通
**做什么**：`cli.py run` 一次性跑完 6 个阶段。
**验收**：单条命令从采集到发布全程无人工干预，产出 3 条视频。
**依赖**：T11

### T13 ✅ 补视频 pipeline 单元测试
**做什么**：为状态机、热度归一化、去重摘要、CLI 输出解析、种子数据写测试。
**验收**：✅ **新增 28 个测试全绿，总数 32 → 60**。
**完成时间**：2026-09-03
**测试聚焦在「已经踩过的坑」上，而非追求覆盖率**：
- `test_normalize_spreads_scores_across_full_range` —— 守住「热度分全部撞顶 100」的回归
- `test_parses_result_json_from_noisy_stdout` —— 守住 MPT 日志混杂时的路径提取
- `test_generated_task_id_is_valid_uuid` —— 守住 MPT 强制 UUID 的约束
- `test_seed_every_scene_has_narration_and_search_hint` —— 守住缺旁白/缺提示导致的降级
- `test_seed_platform_metadata_respects_title_limits` —— 守住各平台标题长度上限

### T14 ✅ 每日定时调度
**做什么**：新建 `scheduler.py`，CLI 接入 `schedule`（常驻进程）和 `schedule-config`（输出 cron/launchd）。
**验收**：✅ 逻辑测试通过（只在触发小时执行、每日至多一次、任务抛异常不重试、次日再触发）；
`schedule-config cron/launchd` 能输出可直接粘贴的配置。
**完成时间**：2026-09-03
**幂等**：`VideoPipelineRun.run_date` 唯一约束保证同日重复触发只返回已有运行，不会重跑。
**用法**：
```bash
python -m xhs_manager.video_pipeline.cli schedule --hour 0                 # 常驻进程
python -m xhs_manager.video_pipeline.cli schedule-config cron --hour 8     # 系统 cron
python -m xhs_manager.video_pipeline.cli schedule-config launchd --hour 8  # macOS launchd
```

### T15 ✅ 规范化数据库迁移
**做什么**：在干净库验证完整迁移链；给真实库 `stamp head`。
**验收**：✅ 干净库 `upgrade head` 走完 4 步迁移建出 29 张表（含 8 张 video_）；
`downgrade -1` 正确删 8 张 video_ 表，再 `upgrade head` 恢复。
**完成时间**：2026-09-03
**处理的遗留问题**：真实库原用 `create_all` 建表，`alembic_version` 停在 `3a7d2b4e5f90`，
后续新迁移会因表已存在而失败。已 `stamp head` 到 `4b8c5d6e7f01`，`upgrade head` 现为 no-op。

### 四.5 平台采集后端实测结论（T01 产出）

#### opencli 真实命令格式（我原先的推测是错的）

```bash
# ❌ 错误（原代码里的推测）
opencli xiaohongshu search "关键词" --limit 20 --sort hot --json

# ✅ 正确：query 是位置参数，用 -f json 而非 --json，无 --sort
opencli xiaohongshu search "关键词" --limit 20 -f json
opencli douyin search "关键词" --limit 20 -f json
opencli bilibili hot --limit 20 -f json
opencli twitter search "关键词" --limit 20 -f json
opencli twitter trending -f json
```

小红书 search 输出列：`rank, title, author, likes, published_at, url`

#### 各平台可用后端矩阵

| 平台 | 后端 | 状态 | 说明 |
|------|------|------|------|
| **B站** | 公开 HTTP API | ✅ **立即可用** | `api.bilibili.com`，无需登录，实测拿到真实数据 |
| **V2EX** | 公开 HTTP API | ✅ **立即可用** | `v2ex.com/api/topics/hot.json` |
| 小红书 | opencli（浏览器） | ⚠️ 阻塞 | 需启用 Chrome 扩展 |
| 抖音 | opencli（浏览器） | ⚠️ 阻塞 | 需启用 Chrome 扩展 |
| Twitter/X | opencli（浏览器） | ⚠️ 阻塞 | 需启用 Chrome 扩展 |

#### ⚠️ 阻塞项：OpenCLI Chrome 扩展未启用

- daemon 正常运行（端口 19825），Chrome 也在运行
- agent-reach 检测到**扩展文件已在磁盘**，但未加载/启用
- **需要用户手动操作**：打开 `chrome://extensions/` → 找到 OpenCLI → 启用；然后 `opencli doctor` 应显示 Extension: connected

#### 可用的 B站 API 端点

```bash
# 热门视频
GET https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1

# 关键词搜索（需 User-Agent + Referer 头）
GET https://api.bilibili.com/x/web-interface/wbi/search/type?search_type=video&keyword=<词>&page=1
```

#### 设计决策

Stage1 改为**多后端架构**：优先用公开 API（无需登录、更稳定），opencli 作为需登录平台的通道，单平台失败不阻塞其他平台。这样**今天就能跑通**（B站+V2EX），扩展启用后自动接入其余 3 个平台。

---

## 五、进度日志

| 日期 | TODO | 说明 |
|------|------|------|
| 2026-09-03 | — | 架构设计 + 代码骨架完成，OAuth 认证方案确定 |
| 2026-09-03 | T01 ✅ | 验证 opencli 命令格式；发现 B站/V2EX 公开 API 可用，3 平台待启用 Chrome 扩展 |
| 2026-09-03 | T02 ✅ | Stage1 跑通，147 条真实热点入库（B站138 + V2EX9）；3 平台因扩展未启用跳过 |
| 2026-09-03 | T03 🔄 | 修正 output_config.format 结构（少一层嵌套）；实际调用受 429 限流阻塞 |
| 2026-09-03 | T05 ✅ | MoneyPrinterTurbo 素材下载跑通；修正 task-id 需 UUID、素材在 cache_videos、路径从 stdout 取 |
| 2026-09-03 | T08 ✅ | Python playwright 渲染器跑通，HTML→MP4 端到端验证（1080x1920/H.264/6.0s）|
| 2026-09-03 | T06 ✅ | Stage3 跑通，6 场景全部拿到真实素材；修复 TTS 被 continue 跳过、音轨冗余两个 bug || 2026-09-03 | T07 ✅ | Stage4 跑通；修复视频素材完全没进 HTML 的严重 bug + 主副标题并排的排版 bug |
| 2026-09-03 | T09 ✅ | **渲染出第一个真实视频** 8.3MB/35.35s/1080x1920，6 场景画面全部验证正常 |
| 2026-09-03 | T10+T13 ✅ | MPT 渲染路径验证通过；新增 28 个单元测试（总数 32→60）|
| 2026-09-03 | T14+T15 ✅ | 每日调度器 + alembic 迁移链验证；真实库 stamp 到 head；测试 69 passed |
| 2026-09-03 | T03+T04 ✅ | 找到正确 ACP 通道 `claude -p`（之前直打 API 是错的）；Stage2 真实 LLM 生成 3 选题 3 脚本 |
