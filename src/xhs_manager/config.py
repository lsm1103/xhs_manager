from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="XHS_",
        extra="ignore",
    )

    env: str = "development"
    database_url: str = "sqlite:///./data/xhs_manager.db"
    operator_timezone: str = "America/Los_Angeles"
    internal_api_token: str = ""

    feishu_custom_bot_webhook: str = ""
    feishu_verification_token: str = ""
    feishu_allowed_user_ids: list[str] = Field(default_factory=list)

    publishing_enabled: bool = False
    comments_enabled: bool = False

    @field_validator("feishu_allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @model_validator(mode="after")
    def validate_safety_boundaries(self) -> "Settings":
        if self.is_production and not self.internal_api_token:
            raise ValueError("生产环境必须配置内部接口令牌")
        if self.is_production and not self.feishu_verification_token:
            raise ValueError("生产环境必须配置飞书回调校验令牌")
        if self.publishing_enabled or self.comments_enabled:
            raise ValueError("当前开发阶段尚未实现真实发布和评论执行器")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
