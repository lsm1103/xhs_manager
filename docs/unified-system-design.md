# 统一系统设计：一个任务模型，两种内容形态

> 目标读者：你自己（单人、本机、macOS）
> 状态：设计稿，待拍板后动工
> 取代：[console-design.md](console-design.md) 第 6 节"不把内容运营主系统搬进来"的结论
> 相关：[video-pipeline-design.md](video-pipeline-design.md)

## 0. 结论先行：这不是"合并三个系统"

盘完代码之后，我要先更正上一版设计里的一个判断。我原来说"两条线没打通，
硬塞进一个控制台只会让两边都难用"——**这个判断的前提是错的**。

真实情况是：

1. **视频工厂和内容运营主系统，早就在同一个数据库、同一个 SQLAlchemy `Base` 里。**
   `data/xhs_manager.db` 一共 29 张表，两边的表都在里面。
   `video_pipeline/models.py` 第 22 行就是 `from xhs_manager.models import Base`。
2. **但它们之间零耦合。** video_pipeline 里没有任何一处引用 `content_tasks` 或 `accounts`；
   主系统的 `services.py / api.py / worker.py` 里没有任何一处出现 "video"。
3. **主系统早就给视频留好了插槽。** `services.py:1408` 已经在派发
   `step_type="produce_content"` 的工作项——**而这个步骤目前没有真正的实现**。
   video_pipeline 就是它缺的那个实现。

所以这件事不是"合并三个系统"，是**把一根已经留好的插头插上**。
不需要数据迁移，不需要合库，主要工作是加几个外键、把 6 个阶段登记成工作项、
以及做一套统一界面。

---

## 1. 三者的真实关系

| | 视频工厂 `video_pipeline` | 内容运营主系统 | TTS Studio |
|---|---|---|---|
| 位置 | 同一个 DB，8 张表 | 同一个 DB，20 张表 | 独立 SQLite + 独立进程 |
| 入口 | CLI 11 个子命令 | REST 26 个端点 | Web UI :8420 |
| 有什么 | **真实的生产引擎**：采集 / 选题 / 素材 / 配音 / 渲染 / 发布 | **治理骨架**：账号、策略版本、审批、队列、重试、幂等、排期、审计 | 模型常驻、试听、A/B |
| 缺什么 | 账号、审批、队列、重试、幂等、排期、审计——**全都没有** | 生产能力——`produce_content` 是空的，`asset_paths` 只是个字符串列表 | 与前两者无关联 |

**两边各自缺的，正好是对方有的。** 这是合并最硬的理由，比"界面统一"强得多。

### 合并后视频线白捡到的

- **审批**：`approval_requests` 是通用的（`resource_type` + `resource_id` + `resource_version`），
  审一个视频脚本和审一个选题提案没有区别。视频线现在是零审批，渲染完直接发。
- **持久化队列**：`work_items` 带租约、重试次数、`available_at`、幂等键。
  视频线现在是 CLI 同步跑，进程一挂就断在半路。
- **账号与策略版本**：视频线的品牌名现在硬编码在 `config.py` 的 `brand_name` 里。
- **排期与幂等发布**：`publication_plans` 有 `scheduled_at / allowed_from / allowed_until /
  idempotency_key`，视频线的发布是"跑到就发"。
- **审计**：`audit_logs` / `external_actions`。

### 合并后图文线白捡到的

- 一条能真出片的生产链：13 平台采集、素材搜索 + 肖像权过滤、TTS、HTML 组合、逐帧渲染。
- `ContentVersion.cover_script` / `slide_scripts` 这两个字段本来就是为卡片设计的，
  现在能被 `composition/` 那套版面引擎真正渲染出来。

---

## 2. 目标模型：一个任务，两种形态

主干就用现成的 `ContentTask` 状态机，**视频的 6 个阶段整体嵌进 `PRODUCING` 这一态**：

```
pending_research → researching → pending_topic_approval → PRODUCING
                                                            │
                          ┌─────────────────────────────────┘
                          │  format = article：产出 content_version（卡片）
                          │  format = video  ：跑视频 6 阶段
                          │     materializing → composing → rendering
                          └─────────────────────────────────┐
                                                            ↓
     quality_checking → pending_publish_approval → scheduled → publishing → published
```

### 2.1 表怎么对上

| 概念 | 主系统 | 视频线 | 合并策略 |
|---|---|---|---|
| 采集批次 | `research_runs` | `video_pipeline_runs` | **保留两张**，`video_pipeline_runs` 加 `research_run_id` 外键 |
| 信号 | `research_signals`（可信度/时效/版权风险/核验态） | `video_trend_signals`（平台/互动量/热度分） | **保留两张 + 一个统一视图**。字段集合不同是真实差异，不是重复 |
| 选题 | `topic_proposals`（可审批、带版本） | `video_topics`（评分、角度、why_now） | `video_topics` 加 `topic_proposal_id`；审批只走主系统那张 |
| 内容 | `content_versions`（`slide_scripts` / `cover_script`） | `video_scripts`（`scenes`） | `video_scripts` 加 `content_version_id`；`recommended_format='video'` |
| 发布 | `publication_plans`（排期 + 幂等 + 审批） | `video_publications`（平台产物） | `video_publications` 加 `publication_plan_id`，排期归主系统 |
| 编排 | `work_items`（租约/重试/幂等） | 无 | **视频 6 阶段登记为 6 种 `step_type`** |

**要点：不迁移数据、不删表、不改现有列。** 只加外键（可空），存量数据保持能用。

### 2.2 `produce_content` 的实现

`services.py:1408` 已经在派发这个工作项，现在把它实现成：

```
produce_content(task)
  ├─ format == "article" → 现有图文生成路径
  └─ format == "video"   → 依次入队 6 个 work_item
                            collect / select / materialize / compose / render / publish
```

每个阶段就是一个 `work_item`，天然获得：失败重试、租约防重入、
幂等键防重复渲染、`error_code` / `error_detail` 落库。

**视频阶段函数本身一行不用改**——`pipeline.run_stage()` 的签名已经是
`(run_id, stage) → dict`，正好是一个工作项处理器该有的样子。

---

## 3. 进程与部署

| 服务 | 端口 | 合并后 |
|---|---|---|
| 主业务 API + 控制台 | 8000 | **合并**：同一个 FastAPI，`/v1/*` 是 API，`/console/*` 是界面 |
| Worker | — | 常驻进程，消费 `work_items`，图文和视频共用 |
| TTS Studio | 8420 | **保持独立进程**，界面里作为一个面板通过 HTTP 聚合 |

TTS Studio 不并进主进程的理由是硬的：它要常驻加载十几 GB 的模型，
生命周期和重启代价跟 Web 服务完全不同。主进程重启一次是秒级，
TTS Studio 重启一次是分钟级。**把它们绑在一个进程里，等于每次改代码都要重新加载模型。**
界面上统一，进程上分开。

---

## 4. UI 设计

### 4.1 导航按"内容的一生"分，不按"三个系统"分

左侧固定导航，五项：

```
任务台      所有内容任务，图文和视频在同一张表里
采集        信号浏览器（两类信号统一视图，按平台/时效/可信度筛选）
资产        成片、封面、卡片、素材、音轨，按任务归档
工具        一页体检所有外部依赖 + TTS Studio 面板
设置        账号、策略版本、调度、配置快照
```

**绝不能按"视频工厂 / 内容运营 / TTS"分栏**——那只是把后端的目录结构
原样暴露给使用者，等于没合并。

### 4.2 核心页：任务详情

一条竖向主干，每一节点是任务状态机的一态，展开即该态的产物与决策点：

| 节点 | 展开内容 | 决策点 |
|---|---|---|
| 调研 | 各平台后端 / 状态 / 降级原因、信号条数 | — |
| 选题 | 提案正文、评分、引用证据、反方观点 | **批准 / 打回** |
| 制作 | **图文**：卡片逐屏预览<br>**视频**：6 阶段子流水线（见下） | — |
| 质检 | 一致性校验、肖像权拦截记录、版权风险 | — |
| 发布审批 | 最终标题/正文/标签/封面 | **批准 / 打回** |
| 排期 | 计划时间、允许窗口、幂等键 | 改期 |
| 发布 | 平台结果、外链、失败原因 | 重试 |

视频任务的"制作"节点展开后是子流水线，每段展开即产物：
采集降级原因 / 逐场景表（layout · 声明时长 · 实际语音时长 · 旁白 · 屏幕文字）/
被肖像权剔除的素材 / `index.html` 内嵌 iframe 带时间滑块（调页面现成的
`window.__seek(t)`）/ 成片内嵌播放。

顶部一条**一致性校验**：脚本 / 语音 / 组合 / 成片四个总时长并排，对不上标红。
（上一支视频的音画错位，就是这四个数字里 195 ≠ 168 造成的。）

### 4.3 设计语言

- 调色板取自项目自己的 `tech_night` 主题：`#05060a` 底 / `#3ddc97` 主色 / `#4d8cff` 次色。
  **控制台应该看起来像它管理的那台机器。**
- 字体：Latin 与数字用 IBM Plex Sans / Plex Mono，中文回落 PingFang SC——
  与 `composition/theme.py` 的字体栈一致。
- 状态色独立于主色：正常 / 降级 / 失败 三档，不与品牌色混用。
- 技术栈沿用 TTS Studio 的范式：**FastAPI 直接挂静态文件，vanilla JS，零构建**。
  它用 403 行做出了可用界面，这套 UI 的交互复杂度不高于它。

---

## 5. 分期

| 期 | 内容 | 为什么是这个顺序 | 估时 |
|---|---|---|---|
| **P0** ✅ | 只读界面：任务台 + 任务详情（含视频子流水线）+ 一致性校验 | 先让两条线在同一个界面里可见，不改任何后端行为 | 已完成 |
| **P1** ✅ | 工具体检页 + TTS Studio 面板聚合 | 几乎不写新逻辑，全是并联已有探测函数 | 已完成 |
| **P2** ✅ | 加外键：`video_topics.task_id` + 认领工具（adopt / unlink） | 纯 schema 变更，存量数据不动 | 已完成 |
| **P3** ✅ | 实现 `produce_content` 的 video 分支，6 阶段登记为 `work_item` | 视频线拿到队列、重试、幂等 | 已完成 |
| **P4** | 视频走审批与排期；采集/资产浏览器 | 视频线拿到审批和排期 | ~1 天 |

P0+P1 不碰后端，纯增量，风险最低，先做。P2 之后两条线才算真打通。

---

## 6. 风险与边界

- **最大风险是"假合并"**：做成一个左侧三个入口的壳子，各点各的。
  防御办法是 P0 就以 **Task 为唯一主对象**建界面，不给 video_pipeline 单独的顶级入口。
- **存量视频 run 没有 task**：已按「允许 `task_id` 为空 + 界面标成游离任务」实现，
  并提供 `cli adopt` 显式认领。认领按视频的**真实进度**落到对应任务状态，
  且**不入队任何工作项**——被认领的往往是早就渲染完甚至发布完的片子，
  走 `services.create_content_task` 会让 worker 去给做完的东西重做调研。
- **不做的**：多用户与权限、云端部署、视频时间轴拖拽剪辑器
  （"剪辑"在这里等于"改脚本 JSON + 重跑 composing"）、通知中心（飞书已有）。
