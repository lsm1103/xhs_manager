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
    max_duration: int = 90                      # 单视频最大时长（秒）
    min_duration: int = 30                      # 单视频最小时长（秒）

    # ── 热点采集 ──
    # 采集平台。bilibili/v2ex 走公开 API（免登录）；
    # xiaohongshu/douyin/twitter 走 opencli，需 Chrome 扩展已启用
    trend_platforms: list[str] = Field(
        default_factory=lambda: [
            "bilibili", "v2ex", "xiaohongshu", "douyin", "twitter",
        ]
    )
    trends_per_platform: int = 20               # 每个平台采集的热点数
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
    tts_provider: str = "edge"                  # edge | azure | openai
    tts_voice: str = "zh-CN-XiaoxiaoNeural"     # 默认中文女声
    tts_rate: str = "+10%"                      # 语速

    # ── 渲染 ──
    render_fps: int = 30
    render_resolution: str = "1080x1920"        # 竖版

    # ── 发布 ──
    publish_platforms: list[str] = Field(
        default_factory=lambda: ["xiaohongshu"]
    )
    publish_delay_minutes: int = 5              # 平台间发布间隔

    # ── 输出目录 ──
    output_base_dir: str = "data/video_pipeline"

    @property
    def pixelle_available(self) -> bool:
        return bool(self.pixelle_path)


def get_video_settings() -> VideoPipelineSettings:
    return VideoPipelineSettings()
