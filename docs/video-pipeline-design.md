# 视频自动化 Pipeline 设计文档

> 状态：草案 v0.1 | 2026-09-03

## 1. 目标

构建一条**全自动每日视频内容流水线**：

```
热点采集 → 选题+脚本 → 素材收集 → HTML动态构建 → 录制视频 → 多平台发布
```

每天自动产出 **3 条短视频**，发布到小红书、抖音、B站、X(Twitter) 四个平台。

### 1.1 关键约束

- 与现有图文 pipeline 共享数据库和基础设施（队列、审批、审计），但业务逻辑独立
- 全自动运行，人只看结果（但保留紧急暂停能力）
- 视频采用 HTML 组合 → 屏幕录制方案，最大化创意灵活性
- 素材来源：Pixelle-Video Public + MoneyPrinterTurbo + 生图模型

---

## 2. Pipeline 六阶段详设

### Stage 1: 热点采集 (`collect_trends`)

**输入**: 无（定时触发）
**输出**: 聚合热点信号列表

```
┌─────────────────────────────────────────────────────┐
│  每日定时触发（默认 UTC 00:00 = 北京 08:00）         │
│                                                      │
│  每个平台是一条**后端链**，逐级降级而不是整体归零：    │
│    站内 API  →  登录态通道  →  站外索引               │
│                                                      │
│  bilibili   B站公开 API              → 索引          │
│  v2ex       V2EX API + sov2ex 全文    → 索引          │
│  toutiao    头条热榜 API（搜索走索引）                │
│  baidu      百度热搜 API                              │
│  wechat     搜狗微信搜索（免登录）                    │
│  zhihu      ZHIHU_COOKIE → 浏览器站内 → 索引          │
│  weibo      WEIBO_COOKIE → 浏览器站内 → 索引          │
│  xiaohongshu / douyin / twitter                      │
│             opencli → 浏览器站内 → 索引               │
│  juejin / 36kr / web   仅索引                         │
│                                                      │
│  聚合去重 → 平台内排名归一 → 存入数据库                │
└─────────────────────────────────────────────────────┘
```

**技术方案**:
- 后端链定义在 `integrations/collectors.py:PLATFORM_BACKENDS`，
  采集结果带 `backend` / `status` / `notes`，降级原因可观测。
- 站外索引 = DuckDuckGo（支持 `site:`）+ 360 兜底，
  **严格校验域名**：跨站结果宁可丢弃，也不冒充成该平台的样本。
  两个引擎都被风控时会如实标记不可用，而不是报"无结果"。
- 登录态通道有两条：`site-login <platform>` 扫码（项目自带 Playwright +
  常驻 profile，不依赖第三方扩展）、或配 `ZHIHU_COOKIE` / `WEIBO_COOKIE`。
- 定向话题调研：`stage collecting --platforms ... --keywords ...`，
  此时默认不取平台热榜（当日泛热点会稀释话题信号）。
- 随时诊断：`cli collect-doctor`。
- 信号存入 `video_trend_signals` 表，含平台、热度指标、原文摘要。

### Stage 2: 选题 + 脚本生成 (`select_topics`)

**输入**: 热点信号列表
**输出**: 3 个视频选题，每个含完整分镜脚本

```
┌─────────────────────────────────────────────────────┐
│  LLM（Claude API）分析热点信号                        │
│                                                      │
│  Step 2a: 选题筛选                                   │
│  ├─ 从 40-80 条热点中提取 10 个候选话题               │
│  ├─ 按 5 维度评分：热度 × 独特性 × 可视化性           │
│  │   × 时效性 × 平台适配度                            │
│  └─ 选出 top 3                                      │
│                                                      │
│  Step 2b: 脚本生成（每个选题）                        │
│  ├─ 视频类型判定（口播/混剪/图文动画/数据可视化）       │
│  ├─ 分镜脚本（5-10 个场景）                           │
│  │   每个场景含：                                     │
│  │   - 时长（秒）                                     │
│  │   - 画面描述（用于素材搜索 + 生图）                 │
│  │   - 文字叠加内容                                   │
│  │   - 转场效果指令                                   │
│  │   - 旁白/配音文本                                  │
│  ├─ 整体时长控制（30-90秒）                           │
│  ├─ BGM 风格建议                                     │
│  └─ 各平台适配的标题/说明/标签                        │
└─────────────────────────────────────────────────────┘
```

**技术方案**:
- Claude API 进行选题评分和脚本生成
- 输出结构化 JSON，存入 `video_topics` + `video_scripts` 表
- 脚本格式兼容 HyperFrames STORYBOARD.md

### Stage 3: 素材收集 (`collect_materials`)

**输入**: 每个视频的分镜脚本
**输出**: 每个场景的素材文件（视频片段 + 生成图片）

```
┌─────────────────────────────────────────────────────┐
│  对每个视频的每个分镜场景：                            │
│                                                      │
│  并行执行 3 条素材获取路径：                           │
│                                                      │
│  路径 A: Pixelle-Video Public                        │
│  ├─ 基于场景画面描述搜索视频片段                       │
│  └─ 下载匹配的 1-3 个候选片段                         │
│                                                      │
│  路径 B: MoneyPrinterTurbo                           │
│  ├─ 调用其素材搜索（Pexels API）                      │
│  └─ 获取免版权视频素材                                │
│                                                      │
│  路径 C: 生图模型                                    │
│  ├─ 对无法找到合适视频素材的场景                       │
│  ├─ 使用 baoyu-image-gen 生成静态画面                 │
│  └─ 或生成自定义插图/图表/数据可视化                   │
│                                                      │
│  素材选择器：                                         │
│  ├─ 从候选素材中选择最佳匹配                           │
│  ├─ 视频片段裁剪到场景时长                            │
│  └─ 图片添加 Ken Burns 效果参数                       │
└─────────────────────────────────────────────────────┘
```

**技术方案**:
- Pixelle-Video Public CLI/API 调用
- MoneyPrinterTurbo 的 `material_search` 模块
- `baoyu-image-gen` skill 生成兜底图片
- 素材文件存储在 `data/video_assets/{pipeline_run_id}/{video_id}/` 下
- 素材元数据（来源、许可证、路径）存入 `video_materials` 表

#### 自带素材：`local:` 提示

搜索路径解决的是「我需要一个画面来表达某个抽象概念」。但有一类片子不是这样：
**介绍自己做的工具**时，画面必须是你自己的产品截图和录屏——去 Pexels 搜不到，
搜到了也不是你的东西。

脚本里把素材提示写成 `local:` 就能点名本地文件：

```json
"material_hints": ["local:videos/quick-logcat/assets/ui_full.png"]
```

- 相对路径按 `XHS_VIDEO_LOCAL_MATERIAL_DIRS`（默认 `["."]`）依次找，绝对路径直接用。
- 认领发生在**所有搜索之前**。点了名的场景后续一律跳过，不会白调一次 MPT。
- 图片会顺手探出像素尺寸写进 `video_materials.width/height`，
  截图特写版面靠它把像素框换算成比例框。
- 文件找不到会打 WARNING 并退回搜索路径。不静默是故意的：
  路径写错的后果是画面悄悄变成一段不相干的 Pexels 素材，只看日志才查得出来。

**这里不跑人脸检测**，和搜索路径不同。那一层防的是「搜索返回了什么我不知道」——
Pexels 的免版权授权不覆盖被拍摄者的肖像权，所以结果侧必须兜底（见
`portrait_guard.py`）。`local:` 指的是运营自己挑的文件，挑没挑对是他的判断，
不该由这一层替他否决。

`local:` 只在**手写脚本**里用（`cli seed --script`）——LLM 不可能知道你机器上
有哪些文件，所以 Stage 2 的 prompt 里没有这个前缀。

#### 不要背景图：`"none"` 提示

```json
"material_hints": ["none"]
```

hook / bullets / compare 这类版面把素材当**全幅底图**。Pexels 那种柔焦素材适合
这么用，UI 截图不适合：截图里的小字和大标题叠在一起，谁都看不清。这类场景要的
是主题的渐变底纹，而不是「随便找张图垫着」。

- 拦在 Stage 3 而不是 Stage 4。兜底链最后一定会生成一张文字卡片，
  只靠组合阶段跳过的话，那张卡片还是白生成了一遍。
- 弃权的场景**不进 pending**。进了会在补位那一步被别处的余料顶上，
  等于绕过了这个声明。
- 「一条素材都没收到」的失败判据会扣掉弃权的场景：整条片子都是文字卡时
  `on_hand` 本来就是 0，那是作者的选择，不是收集失败。
- `"none"` 和其它提示同时出现，`seed.load_script_file` 直接拒收——
  运行期虽然以 `none` 为准，但作者显然不是这个意思，多半是改脚本时忘了删旧的那行。

### Stage 4: HTML 动态构建 (`compose_html`)

**输入**: 分镜脚本 + 素材文件
**输出**: 可播放的 HTML 组合文件

```
┌─────────────────────────────────────────────────────┐
│  HyperFrames HTML 组合构建                           │
│                                                      │
│  每个视频 = 一个 HTML 文件：                           │
│  ├─ <div class="clip" data-start="0" data-duration="5"> │
│  │   ├─ 背景：视频素材 / 生成图片 / 渐变              │
│  │   ├─ 文字层：标题、关键数据、引用                   │
│  │   ├─ 动画：CSS keyframes / GSAP                   │
│  │   └─ 转场：淡入淡出/滑动/缩放/故障效果             │
│  ├─ 音轨：BGM + 旁白（TTS 生成）                     │
│  └─ 尺寸：1080×1920（竖版）                          │
│                                                      │
│  转场效果库：                                         │
│  ├─ fade（淡入淡出）                                  │
│  ├─ slide-left / slide-right（左右滑动）              │
│  ├─ zoom-in / zoom-out（缩放）                       │
│  ├─ glitch（故障风）                                  │
│  ├─ blur-transition（模糊过渡）                       │
│  ├─ wipe（擦除）                                     │
│  ├─ flip（翻转）                                     │
│  └─ morph（形变过渡）                                │
│                                                      │
│  文字动画库：                                         │
│  ├─ typewriter（打字机效果）                          │
│  ├─ pop-in（弹入）                                   │
│  ├─ slide-up（上滑出现）                             │
│  ├─ counter（数字滚动）                               │
│  └─ highlight（高亮划线）                             │
└─────────────────────────────────────────────────────┘
```

**技术方案**（已实现，见 `video_pipeline/composition/`）:

- **确定性时间轴**：所有动画 `animation-play-state: paused`，
  进度由 `animation-delay: calc((var(--s) - var(--t)) * 1s)` 决定。
  渲染器每帧只写 `:root { --t }`，画面因此是 `--t` 的纯函数。
  这是逐帧截图渲染能正确出片的前提——CSS 动画默认走墙钟，
  而两帧之间的真实耗时不确定，不锁住就会渲染出「动画瞬间结束」的画面。
- **版面模板**（`layouts.py`）：hook / statement / stat / quote / bullets /
  compare / screenshot / scroll / terminal / outro，按内容特征自动推断，
  也可由脚本显式指定 `layout`。
- **贯穿外壳**（`builder.py`）：品牌条、章节角标、分段进度条、字幕带、
  水印、颗粒 + 暗角；按平台 UI 留安全区。
- **主题**（`theme.py`）：tech_night / warm_paper / electric，颜色字阶集中一处。
- **样式**：`assets/base.css`，占位符在 build 时替换后内联进产物。
- 输出 HTML 存储在 `{output_base_dir}/{run_id}/{script_id}/composition/index.html`

**预览**：`python -m xhs_manager.video_pipeline.composition.preview`，
不跑流水线就能看版面；浏览器打开加 `#preview` 才自动播放。

#### `screenshot` 版面：界面特写

给「介绍自己做的工具」用。问题很具体：横向的界面截图缩到竖屏宽度后只有
1080×435，界面上那个要讲的控件只剩十几像素高——整图放上去等于什么都没给看清。
所以这个版面是**整图 + 高亮框 + 局部放大卡**三件套：

```json
{
  "layout": "screenshot",
  "material_hints": ["local:videos/quick-logcat/assets/ui_full.png"],
  "focus": [
    {"at": 1.2, "rect": [6, 6, 280, 32],
     "label": "真机 / 模拟器 随时切", "note": "设备下拉里选，连上就开始收"},
    {"at": 4.4, "rect": [8, 44, 240, 26], "label": "级别过滤"}
  ]
}
```

- `rect` 是 `[x, y, w, h]`，**按源截图的像素**量——这是量坐标时最自然的单位。
  编译时用素材的真实尺寸换算成比例，所以换画布尺寸不用改脚本。
  四个数全 ≤ 1 时按比例解释（探不出源图尺寸时的退路）。
- `at` 不给就按时长均分；`hold` 不给就停到**下一个特写出现**为止，
  最后一个停到场景结束前。旁白改了导致场景时长变化时，特写会自己跟着伸缩——
  Stage 3 会按真实语音时长回写 `duration`，硬编码 hold 就会错位。
- 放大倍数默认由框宽推出来（让这块正好铺满放大卡），夹在 1.5–8 倍之间；
  要手动指定就给 `zoom`。
- 放大卡的定位没有任何像素计算：`left/top:50%` 定锚点，
  `transform: translate(-cx%, -cy%)` 的百分比参照 img 自身尺寸，
  于是「把框心挪到卡片正中」就是一句 translate。
- 背景不再走 Ken Burns，改成同一张图糊开当底纹——前景已经有一张完整静态截图了，
  背景再动就是两张同样的图在打架。

写错了会当场报错而不是静默退化：`layout` 拼错、`rect` 少一个数、宽高为 0，
`seed.load_script_file` 都会拒收。手写脚本和 LLM 产物在这点上要求相反——
LLM 产物宁可退化也别炸掉整条流水线，手写脚本则必须早报错，
不然你要渲染完整片才发现高亮框一个都没出。

#### `scroll` 版面：长截图滚动

网页整页、长文档、README ——一张 2880×9074 的长截图塞进竖屏只能看到顶部一条。
这个版面把它放进定高视窗里缓缓上移。

```json
{
  "layout": "scroll",
  "window": "github.com/lsm1103/quick_logcat",
  "material_hints": ["local:videos/quick-logcat/assets/gh_full.png"],
  "scroll": {"end": 0.32, "aspect": 1.25},
  "focus": [{"at": 1.6, "rect": [2612, 178, 210, 62], "label": "顺手点个 Star"}]
}
```

- `scroll.end` 是**图片自身高度的比例**：0.32 = 整张图上移了自身高度的 32%。
  选这个单位是因为拿图片高度一除就能估出来，不用先知道视窗多大。
  不给就走到底。
- 行程会被**夹在可见范围内**，滚不出底边。可见比例 = `imgW / (aspect × imgH)`，
  剩下的才是能滚的。滚过头的画面是一整屏空白，比少滚难看得多。
- `aspect` 是视窗宽高比，由 Python 写进 inline style —— 只有这一个出处。
  CSS 里再写一份的话，两边对不上就会滚过头或滚不到底。
- 整段滚动就是一条 `translateY` 动画，起止由 `--travel` 决定，进度照旧由 `--t` 驱动。
- **滚动时长跟着场景走**，而场景时长由 Stage 3 按真实语音回写。旁白改短了，
  滚动会自己跟着加快，不需要回来改脚本。
- `focus` 在这里只出放大卡、不出高亮框：画面一直在动，框要跟上就得逐帧算位置，
  那就不是纯 CSS 了。

#### `terminal` 版面：命令打字机

```json
{
  "layout": "terminal",
  "text_overlay": {"main": "npx quick-logcat", "sub": "本地起个 Node 服务。"},
  "terminal": {"prompt": "$", "output": [
    "[server] http://localhost:5174",
    "[adb] 1 device connected"
  ]}
}
```

命令逐字敲出来，敲完之后输出一行行出现。「一条命令就能跑」这件事，
演一遍比写在标题里有说服力——观众看到的是命令有多短，而不是一句"很简单"。

- `command` 不给就用 `text_overlay.main`，绝大多数时候这俩就是同一句话。
- 打字速度按字数算（0.075 秒/字符，夹在 0.5–2 秒），短命令不会被拖满整个场景。
- 光标闪烁和背景漂移同理，必须由 `--t` 驱动。漏掉的话它会按截图速度闪，
  同一时间点两次渲染画面不一致。

`screenshot` / `scroll` / `terminal` 三个版面的前景都自带一张主体卡片，
所以背景里的同一张图只当底纹：糊开、压暗、不推拉（`BLURRED_BACKDROP_LAYOUTS`）。
压暗要够狠——blur 半径是屏幕像素，而一张长截图被 cover 到竖屏时只露出很小一块、
放得极大，同样的半径相对它微不足道，白底网页照样能看清字，前景文字就全糊在上面。

`window` 字段（三个版面通用）会画一条假的浏览器/终端标题栏。它一眼说明
「这东西跑在哪」——是浏览器里的 localhost:5174，还是 github.com 上的仓库页，
光一张截图说不清这件事。

### Stage 5: 录制视频 (`render_video`)

**输入**: HTML 组合文件
**输出**: MP4 视频文件 + 封面图

```
┌─────────────────────────────────────────────────────┐
│  HyperFrames CLI 渲染                                │
│                                                      │
│  hf render {composition_dir}                         │
│  ├─ 输入：index.html                                 │
│  ├─ 输出：video.mp4 (1080×1920, H.264)              │
│  ├─ 帧率：30fps                                     │
│  └─ 时长：由 HTML data-duration 属性决定             │
│                                                      │
│  后处理：                                            │
│  ├─ 生成封面图（取第 1 秒或指定帧截图）               │
│  ├─ 生成各平台尺寸的封面（3:4, 16:9, 1:1）           │
│  └─ 压缩（保证 <100MB 满足各平台限制）               │
└─────────────────────────────────────────────────────┘
```

### Stage 6: 多平台发布 (`publish_video`)

**输入**: MP4 + 封面图 + 标题/说明/标签
**输出**: 各平台发布记录（含发布 URL）

```
┌─────────────────────────────────────────────────────┐
│  各平台适配发布                                       │
│                                                      │
│  小红书：                                            │
│  ├─ OpenCLI xiaohongshu save_draft (视频笔记)        │
│  ├─ 标题 ≤20字 + 正文 ≤1000字 + 标签 ≤10个          │
│  └─ 封面 3:4                                        │
│                                                      │
│  抖音：                                              │
│  ├─ ego-browser 自动上传                             │
│  ├─ 标题 ≤55字 + 描述 + 话题标签                     │
│  └─ 封面 9:16                                       │
│                                                      │
│  B站：                                               │
│  ├─ ego-browser 自动上传                             │
│  ├─ 标题 ≤80字 + 简介 + 标签 + 分区选择              │
│  └─ 封面 16:9                                       │
│                                                      │
│  X(Twitter)：                                        │
│  ├─ ego-browser 或 API 发布                          │
│  ├─ 推文 ≤280字 + 视频附件                           │
│  └─ 自动翻译为英文                                   │
└─────────────────────────────────────────────────────┘
```

---

## 3. 数据模型

### 新增表（与现有表共存于同一数据库）

```sql
-- 每日流水线运行
video_pipeline_runs (
  id              TEXT PRIMARY KEY,
  trigger_type    TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled | manual
  run_date        DATE NOT NULL UNIQUE,               -- 运行日期（去重键）
  status          TEXT NOT NULL DEFAULT 'collecting',  -- collecting | selecting | materializing | composing | rendering | publishing | completed | failed
  trend_count     INTEGER DEFAULT 0,
  topic_count     INTEGER DEFAULT 0,
  video_count     INTEGER DEFAULT 0,
  published_count INTEGER DEFAULT 0,
  started_at      DATETIME NOT NULL,
  completed_at    DATETIME,
  error_detail    TEXT,
  created_at      DATETIME NOT NULL
)

-- 采集到的热点信号
video_trend_signals (
  id              TEXT PRIMARY KEY,
  pipeline_run_id TEXT NOT NULL REFERENCES video_pipeline_runs(id),
  platform        TEXT NOT NULL,              -- xiaohongshu | douyin | bilibili | twitter
  source_url      TEXT NOT NULL,
  title           TEXT NOT NULL,
  summary         TEXT NOT NULL,
  heat_score      REAL,                       -- 平台原生热度指标（标准化到 0-100）
  engagement      JSON,                       -- {likes, comments, shares, views}
  author          TEXT,
  published_at    DATETIME,
  content_digest  TEXT NOT NULL,
  tags            JSON DEFAULT '[]',
  collected_at    DATETIME NOT NULL,
  created_at      DATETIME NOT NULL,
  UNIQUE(pipeline_run_id, content_digest)
)

-- 视频选题
video_topics (
  id              TEXT PRIMARY KEY,
  pipeline_run_id TEXT NOT NULL REFERENCES video_pipeline_runs(id),
  rank            INTEGER NOT NULL,           -- 1, 2, 3
  title           TEXT NOT NULL,
  angle           TEXT NOT NULL,              -- 切入角度
  why_now         TEXT NOT NULL,              -- 为什么现在做
  target_audience TEXT NOT NULL,
  video_type      TEXT NOT NULL,              -- explainer | mashup | data_viz | commentary
  estimated_duration INTEGER NOT NULL,         -- 预估秒数
  scores          JSON NOT NULL,              -- {heat, uniqueness, visual, timeliness, platform_fit}
  total_score     REAL NOT NULL,
  source_signal_ids JSON NOT NULL,            -- 来源信号 ID 列表
  status          TEXT NOT NULL DEFAULT 'pending',
  created_at      DATETIME NOT NULL
)

-- 视频脚本
video_scripts (
  id              TEXT PRIMARY KEY,
  topic_id        TEXT NOT NULL REFERENCES video_topics(id),
  version         INTEGER NOT NULL DEFAULT 1,
  total_duration  INTEGER NOT NULL,           -- 总时长（秒）
  scenes          JSON NOT NULL,              -- [{scene_id, order, duration, visual_desc, text_overlay, transition, narration, material_hints}]
  bgm_style       TEXT,
  platform_metadata JSON NOT NULL,            -- {xiaohongshu: {title, desc, tags}, douyin: {...}, bilibili: {...}, twitter: {...}}
  generation_model TEXT NOT NULL,             -- 生成使用的模型
  generation_prompt_hash TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'draft',
  created_at      DATETIME NOT NULL
)

-- 视频素材
video_materials (
  id              TEXT PRIMARY KEY,
  script_id       TEXT NOT NULL REFERENCES video_scripts(id),
  scene_id        TEXT NOT NULL,              -- 对应脚本中的场景 ID
  material_type   TEXT NOT NULL,              -- video_clip | generated_image | stock_photo | chart | text_card
  source_tool     TEXT NOT NULL,              -- pixelle | moneyprinter | pexels | image_gen | manual
  source_url      TEXT,                       -- 原始来源 URL
  local_path      TEXT NOT NULL,              -- 本地文件路径
  license_type    TEXT NOT NULL DEFAULT 'unknown',  -- cc0 | cc_by | editorial | generated | unknown
  duration        REAL,                       -- 视频片段时长（秒），图片为 NULL
  width           INTEGER,
  height          INTEGER,
  file_size       INTEGER,
  selected        BOOLEAN NOT NULL DEFAULT 0, -- 是否被选用
  metadata        JSON DEFAULT '{}',
  created_at      DATETIME NOT NULL
)

-- HTML 组合
video_compositions (
  id              TEXT PRIMARY KEY,
  script_id       TEXT NOT NULL REFERENCES video_scripts(id),
  composition_dir TEXT NOT NULL,              -- 组合文件目录路径
  html_path       TEXT NOT NULL,              -- index.html 路径
  total_duration  REAL NOT NULL,              -- 实际总时长（秒）
  resolution      TEXT NOT NULL DEFAULT '1080x1920',
  transition_effects JSON NOT NULL,           -- 使用的转场效果列表
  has_narration   BOOLEAN NOT NULL DEFAULT 0,
  has_bgm         BOOLEAN NOT NULL DEFAULT 0,
  template_id     TEXT,                       -- 使用的模板 ID
  status          TEXT NOT NULL DEFAULT 'draft', -- draft | validated | render_ready | error
  validation_errors JSON DEFAULT '[]',
  created_at      DATETIME NOT NULL
)

-- 渲染任务
video_renders (
  id              TEXT PRIMARY KEY,
  composition_id  TEXT NOT NULL REFERENCES video_compositions(id),
  output_path     TEXT,                       -- 输出 MP4 路径
  cover_path      TEXT,                       -- 封面图路径
  covers          JSON DEFAULT '{}',          -- {platform: cover_path} 各平台封面
  format          TEXT NOT NULL DEFAULT 'mp4',
  codec           TEXT NOT NULL DEFAULT 'h264',
  fps             INTEGER NOT NULL DEFAULT 30,
  file_size       INTEGER,
  duration        REAL,
  render_time     REAL,                       -- 渲染耗时（秒）
  status          TEXT NOT NULL DEFAULT 'pending', -- pending | rendering | completed | failed
  error_detail    TEXT,
  started_at      DATETIME,
  completed_at    DATETIME,
  created_at      DATETIME NOT NULL
)

-- 发布记录
video_publications (
  id              TEXT PRIMARY KEY,
  render_id       TEXT NOT NULL REFERENCES video_renders(id),
  topic_id        TEXT NOT NULL REFERENCES video_topics(id),
  platform        TEXT NOT NULL,              -- xiaohongshu | douyin | bilibili | twitter
  title           TEXT NOT NULL,
  description     TEXT NOT NULL,
  tags            JSON NOT NULL DEFAULT '[]',
  cover_path      TEXT,
  publish_method  TEXT NOT NULL,              -- opencli | ego_browser | api
  external_id     TEXT,                       -- 平台返回的内容 ID
  external_url    TEXT,                       -- 发布后的 URL
  status          TEXT NOT NULL DEFAULT 'pending', -- pending | uploading | published | failed
  error_detail    TEXT,
  published_at    DATETIME,
  created_at      DATETIME NOT NULL
)
```

---

## 4. Pipeline 状态机

```
                    ┌──────────────────────────────────────┐
                    │         video_pipeline_runs           │
                    │                                      │
  定时触发 ──→ [collecting] ──→ [selecting] ──→ [materializing]
                    │              │                │
                    │              │                ▼
                    │              │         [composing]
                    │              │                │
                    │              │                ▼
                    │              │          [rendering]
                    │              │                │
                    │              │                ▼
                    │              │         [publishing]
                    │              │                │
                    │              │                ▼
                    │              │          [completed]
                    │              │
                    └──────────────┴──→ [failed] (任意阶段可达)
```

每个 stage 的子状态由对应的子表行状态管理（例如每个 video_topic 有自己的 status）。

---

## 5. 目录结构

```
src/xhs_manager/
  video_pipeline/
    __init__.py
    models.py              # 视频 pipeline 专用数据模型
    domain.py              # 状态机、枚举、错误类型
    config.py              # 视频 pipeline 配置
    pipeline.py            # 主编排器（串联 6 个阶段）
    stages/
      __init__.py
      stage1_trends.py     # 热点采集
      stage2_topics.py     # 选题 + 脚本生成
      stage3_materials.py  # 素材收集
      stage4_compose.py    # HTML 动态构建
      stage5_render.py     # 视频录制
      stage6_publish.py    # 多平台发布
    templates/
      base.html            # HTML 组合基础模板
      transitions.css      # 转场效果库
      animations.css       # 文字动画库
      explainer.html       # 讲解型视频模板
      mashup.html          # 混剪型视频模板
      data_viz.html        # 数据可视化视频模板
    integrations/
      __init__.py
      pixelle.py           # Pixelle-Video Public 集成
      moneyprinter.py      # MoneyPrinterTurbo 集成
      trend_collector.py   # 多平台热点采集（封装 agent-reach）
      llm_client.py        # Claude API 调用封装
      tts_client.py        # TTS 语音合成
      publisher_xhs.py     # 小红书发布
      publisher_douyin.py  # 抖音发布
      publisher_bilibili.py # B站发布
      publisher_twitter.py # X 发布
    scheduler.py           # 每日定时触发器
```

---

## 6. 配置项

新增 `.env` 配置（`XHS_` 前缀）：

```env
# === 视频 Pipeline ===
XHS_VIDEO_PIPELINE_ENABLED=true
XHS_VIDEO_DAILY_TRIGGER_HOUR=0        # UTC 时间（0 = 北京 08:00）
XHS_VIDEO_TOPICS_PER_RUN=3
XHS_VIDEO_MAX_DURATION=90              # 单视频最大时长（秒）
XHS_VIDEO_MIN_DURATION=30              # 单视频最小时长（秒）

# 热点采集平台
XHS_VIDEO_TREND_PLATFORMS=xiaohongshu,douyin,bilibili,twitter
XHS_VIDEO_TRENDS_PER_PLATFORM=20

# 素材工具
XHS_PIXELLE_PATH=                      # Pixelle-Video 安装路径
XHS_MONEYPRINTER_PATH=                 # MoneyPrinterTurbo 安装路径
XHS_PEXELS_API_KEY=                    # Pexels API Key（MoneyPrinterTurbo 使用）
XHS_VIDEO_LOCAL_MATERIAL_DIRS=["."]    # 脚本里 local: 相对路径的查找目录

# LLM
XHS_CLAUDE_API_KEY=                    # Claude API Key
XHS_CLAUDE_MODEL=claude-sonnet-5       # 默认使用的模型

# TTS
XHS_TTS_PROVIDER=                      # edge | azure | openai
XHS_TTS_VOICE=                         # 语音 ID

# 发布平台
XHS_PUBLISH_PLATFORMS=xiaohongshu,douyin,bilibili,twitter
XHS_DOUYIN_PUBLISH_ENABLED=false
XHS_BILIBILI_PUBLISH_ENABLED=false
XHS_TWITTER_PUBLISH_ENABLED=false
```

---

## 7. 外部依赖

| 工具 | 用途 | 集成方式 | 状态 |
|------|------|---------|------|
| agent-reach | 多平台热点采集 | Claude Code skill | ✅ 可用 |
| Claude API | 选题评分 + 脚本生成 | HTTP API | 需配置 API Key |
| Pixelle-Video Public | 视频素材搜索 | CLI / API | 待确认安装 |
| MoneyPrinterTurbo | 视频素材搜索（Pexels） | Python 模块调用 | 待确认安装 |
| baoyu-image-gen | 生成兜底图片/图表 | Claude Code skill | ✅ 可用 |
| HyperFrames | HTML 组合构建 + 渲染 | CLI (`hf` 命令) | 需确认安装 |
| media-use | BGM + TTS 旁白 | Claude Code skill | ✅ 可用 |
| OpenCLI | 小红书发布 | CLI | ✅ 已集成 |
| ego-browser | 抖音/B站/X 发布 | Claude Code skill | ✅ 可用 |

---

## 8. 执行时序

```
每日 UTC 00:00 (北京 08:00) 触发
│
├─ 00:00-00:10  Stage 1: 热点采集（4平台并行，每个约2分钟）
├─ 00:10-00:20  Stage 2: 选题+脚本（LLM 3次调用）
├─ 00:20-00:40  Stage 3: 素材收集（3个视频 × 5-10场景，并行下载）
├─ 00:40-00:55  Stage 4: HTML构建（3个视频并行）
├─ 00:55-01:10  Stage 5: 录制（3个视频串行渲染）
├─ 01:10-01:30  Stage 6: 发布（3个视频 × 4平台，错峰发布）
│
└─ 01:30       完成，发送汇总通知
```

预计每日运行总时长：**60-90 分钟**。

---

## 9. 错误处理

- 每个 stage 失败时，pipeline 状态标记为 `failed`，记录 `error_detail`
- 素材收集阶段：单个场景素材获取失败时，用生图模型兜底
- 渲染失败时：最多重试 2 次
- 发布失败时：单个平台失败不阻塞其他平台，标记该 publication 为 `failed`
- 所有外部操作通过现有 `ExternalAction` 机制保证幂等性

---

## 10. MVP 里程碑

### M0: 骨架 + 热点采集（1天）
- [x] 设计文档
- [ ] 数据模型 + 迁移
- [ ] Pipeline 编排器
- [ ] Stage 1 热点采集实现

### M1: 选题 + 脚本（1天）
- [ ] Stage 2 LLM 选题评分
- [ ] Stage 2 视频脚本生成
- [ ] 脚本结构验证

### M2: 素材 + HTML（2天）
- [ ] Stage 3 Pixelle/MoneyPrinterTurbo 集成
- [ ] Stage 3 生图兜底
- [ ] Stage 4 HTML 模板库
- [ ] Stage 4 转场效果库
- [ ] Stage 4 组合构建器

### M3: 渲染 + 发布（1天）
- [ ] Stage 5 HyperFrames 渲染
- [ ] Stage 6 多平台发布适配
- [ ] 每日定时触发

### M4: 稳定化（持续）
- [ ] 监控 + 告警
- [ ] 素材质量评分
- [ ] A/B 测试不同视频风格
