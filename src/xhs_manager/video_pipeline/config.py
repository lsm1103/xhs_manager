"""视频 Pipeline 配置。扩展主配置，使用 XHS_ 前缀。"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class VideoPipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="XHS_VIDEO_",
        extra="ignore",
    )

    # ── 总开关 ──
    pipeline_enabled: bool = False

    # ── 调度 ──
    daily_trigger_hour: int = 0                 # UTC 小时（0 = 北京 08:00）
    topics_per_run: int = 3                     # 每次产出的视频数量
    max_duration: int = 180                     # 单视频最大时长（秒）
    min_duration: int = 30                      # 单视频最小时长（秒）

    # ── 热点采集 ──
    # 采集平台。每个平台是一条后端链（见 integrations/collectors.py）：
    # 站内 API → 登录态通道（opencli / cookie）→ 站外索引，逐级降级。
    # 默认覆盖免登录就能跑通的全部平台；zhihu/weibo 没有 cookie 时走索引。
    trend_platforms: list[str] = Field(
        default_factory=lambda: [
            "bilibili", "v2ex", "toutiao", "baidu", "wechat",
            "zhihu", "weibo", "xiaohongshu", "douyin", "twitter",
        ]
    )
    trends_per_platform: int = 20               # 每个平台采集的热点数
    # 是否连平台热榜一起取。做定向话题调研时置 False，
    # 否则当日泛热榜（明星、体育、社会新闻）会稀释话题信号。
    trend_include_hot: bool = True
    trend_keywords: list[str] = Field(
        default_factory=lambda: ["AI", "人工智能", "大模型", "AI工具", "效率", "自动化"]
    )

    # ── 素材工具 ──
    moneyprinter_path: str = (                  # MoneyPrinterTurbo 安装路径
        "/Users/xm/Desktop/xm_project/code/ai_agent_project/MoneyPrinterTurbo"
    )
    pixelle_path: str = ""                      # Pixelle-Video 安装路径
    pixelle_api_url: str = "http://127.0.0.1:8080"  # Pixelle-Video API 地址
    pexels_api_key: str = ""                    # Pexels API Key（MPT 自带配置则不需要）

    # ── 视频生成模式 ──
    # html: 走我们的 HTML 组合 + 渲染流程（创意灵活）
    # moneyprinter: 直接用 MoneyPrinterTurbo 一站式生成（简单快速）
    # auto: 根据视频类型自动选择
    render_mode: str = "auto"

    # ── LLM（Anthropic Claude API）──
    claude_api_key: str = ""                    # 留空则从 ANTHROPIC_API_KEY 或 ant auth 读取
    claude_model: str = "claude-sonnet-5"       # 日常流水线用 Sonnet 控制成本
    claude_effort: str = "high"                 # low/medium/high/xhigh/max

    # ── TTS ──
    tts_provider: str = "edge"                  # edge | voxcpm | studio
    tts_voice: str = "zh-CN-XiaoxiaoNeural"     # 默认中文女声
    tts_rate: str = "+10%"                      # 语速

    # tts_provider=studio 时走 TTS Studio 服务（模型常驻，免每条片子重复加载）。
    # 服务不可达或模型未 ready 会自动回落到 edge。
    # 配音已并入主服务：同一个进程的 /tts，不再是 :8420 上的独立服务
    tts_studio_url: str = "http://127.0.0.1:8000/tts"
    tts_studio_model: str = "indextts2"
    tts_studio_ref: str = ""                    # data/tts_studio/refs/ 下的文件名
    tts_studio_emotion: str = ""                # 留空则按场景 bgm_mood 映射
    tts_studio_emo_alpha: float | None = None
    tts_studio_ref_text: str = ""
    tts_studio_instruct: str = ""

    # ── 背景音乐 ──
    bgm_enabled: bool = True
    # 曲库目录，逗号分隔。
    # 不再用 MoneyPrinterTurbo 自带的 29 首：那批谱心全在 430-597Hz、
    # energy 0.53-0.65，是同一个低沉氛围风格包，出不了科技感。
    # 而且情绪分类走的是**库内百分位**，两个风格混在一个库里会互相稀释——
    # 科技风的曲子在混合库里未必排得进对应情绪的区间。曲库要保持风格单一。
    bgm_dirs: list[str] = Field(default_factory=lambda: ["data/bgm"])
    bgm_index_path: str = "data/video_pipeline/bgm_index.json"

    # ── 视觉（HTML 组合）──
    # 主题见 video_pipeline/composition/theme.py：tech_night | warm_paper | electric
    composition_theme: str = "tech_night"
    brand_name: str = "AI 工作流实验员"       # 片头左上角
    brand_handle: str = "@ai-workflow-lab"    # 右下角水印

    # ── 渲染 ──
    render_fps: int = 30
    render_resolution: str = "1080x1920"        # 竖版

    # ── 发布 ──
    publish_platforms: list[str] = Field(
        default_factory=lambda: ["xiaohongshu"]
    )
    publish_delay_minutes: int = 5              # 平台间发布间隔
    # manual=不碰浏览器，只在控制台列出要填的内容，人工复制过去
    # draft  =浏览器自动化登录你的号，存成草稿
    # publish=浏览器自动化登录你的号，直接发出去
    #
    # 默认是 manual。draft 和 publish 都会驱动浏览器操作真实账号，
    # 这类自动化已经导致过一次封号——要用得自己明确改配置。
    xhs_publish_mode: str = "manual"
    xhs_browser_session: str = "xhs-video"      # opencli browser 会话名（已弃用）
    xhs_profile_dir: str = ""                   # 小红书专用 Chrome profile，空=~/.xhs_pipeline_chrome
    # 连接已运行的 Chrome（如 http://127.0.0.1:9222）。
    # 小红书草稿存浏览器本地：只有连用户自己的 Chrome，草稿才在用户那边可见。
    xhs_cdp_url: str = ""

    # ── 输出目录 ──
    output_base_dir: str = "data/video_pipeline"

    @property
    def pixelle_available(self) -> bool:
        return bool(self.pixelle_path)


def get_video_settings() -> VideoPipelineSettings:
    return VideoPipelineSettings()
