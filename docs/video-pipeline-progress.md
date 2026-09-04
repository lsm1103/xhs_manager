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
| Stage1 热点采集 | `stages/stage1_trends.py` | 283 | ⚠️ 代码就绪，**未验证** |
| Stage2 选题脚本 | `stages/stage2_topics.py` | 417 | ⚠️ 代码就绪，**未验证** |
| Stage3 素材收集 | `stages/stage3_materials.py` | 455 | ⚠️ 代码就绪，**未验证** |
| Stage4 HTML构建 | `stages/stage4_compose.py` | 568 | ⚠️ 代码就绪，**未验证** |
| Stage5 视频渲染 | `stages/stage5_render.py` | 557 | ⚠️ 代码就绪，**未验证** |
| Stage6 多平台发布 | `stages/stage6_publish.py` | 317 | ⚠️ 仅小红书，其余为桩 |
| Claude 客户端 | `integrations/llm_client.py` | 221 | ✅ OAuth 认证已验证 |
| MoneyPrinterTurbo | `integrations/moneyprinter.py` | 302 | ⚠️ 代码就绪，**未验证** |

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

32 个既有测试通过，视频 pipeline **尚无专属测试**。

---

## 三、核心风险

1. **opencli 未验证** — Stage1（4平台采集）和 Stage6（小红书发布）都依赖它，命令格式全是推测的
2. **全流程从未真实执行** — 所有 stage 只验证了 import，没有跑过真实数据
3. **MoneyPrinterTurbo 参数组合未验证** — `--stop-at materials` 配合 `--video-terms` 是否work未知

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

### T03 🔄 验证 Claude 结构化输出 + OAuth 实际调用
**做什么**：用 OAuth token 调 `output_config.format` 确认结构化输出可用。
**进展**：
- ✅ **修正了 format 结构错误**。API 明确报错指出正确格式：
  ```python
  # ❌ 错误（多套了一层 json_schema）
  "format": {"type":"json_schema", "json_schema":{"name":..., "schema":{...}}}
  # ✅ 正确
  "format": {"type":"json_schema", "schema": {...}}
  ```
- ⚠️ **实际调用受限流阻塞**：修正格式后不再报 400，但持续返回 429。
  原因是当前交互式 Claude Code 会话在重度消耗同一 Max 套餐额度。
- **待办**：在会话空闲时段重试验证。格式正确性已通过「400 消失」间接确认。

### T04 ⬜ Stage2 用真实信号跑通选题+脚本生成
**做什么**：基于 T02 采集到的真实信号，跑 `cli.py stage selecting`。
**验收**：`video_topics` 有 3 条选题，`video_scripts` 有 3 份含 5-10 场景的脚本。
**依赖**：T02, T03

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

### T13 ⬜ 补视频 pipeline 单元测试
**做什么**：为 domain 状态机、模型、pipeline 编排、各 stage 的纯逻辑部分写测试（外部调用 mock）。
**验收**：新增测试全绿，总测试数 > 45。

### T14 ⬜ 每日定时调度
**做什么**：实现 `scheduler.py`，支持按 `daily_trigger_hour` 触发；提供 launchd/cron 配置样例。
**验收**：能配置成每天定时自动跑。
**依赖**：T12

### T15 ⬜ 规范化数据库迁移
**做什么**：把 `create_all` 建的表改为通过 alembic 迁移管理，验证 upgrade/downgrade。
**验收**：`alembic upgrade head` 能在干净库上建出全部表。

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
