"""Claude API 客户端封装 — 使用 Anthropic Python SDK。

认证方式（按优先级自动解析，无需手动配 API Key）:
  1. 显式传入的 claude_api_key
  2. 环境变量 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
  3. Claude Code 的 OAuth 凭证（macOS Keychain，`claude auth login` 后自动可用）

API 规范:
  - 自适应思考: thinking={type: "adaptive"}
  - 结构化输出: output_config.format + json_schema
  - 努力程度: output_config.effort
"""

import json
import logging
import subprocess
import time
from typing import Any, Optional

import anthropic

from xhs_manager.video_pipeline.config import VideoPipelineSettings

logger = logging.getLogger(__name__)

# Claude Code 在 macOS Keychain 中的凭证条目名
KEYCHAIN_SERVICE = "Claude Code-credentials"

# OAuth token 调用 API 时需要的 beta header
OAUTH_BETA_HEADER = "oauth-2025-04-20"


class AuthError(Exception):
    """认证凭证解析失败。"""


def read_claude_code_token() -> Optional[str]:
    """从 macOS Keychain 读取 Claude Code 的 OAuth access token。

    需要先执行 `claude auth login`。返回 None 表示凭证不可用。
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        creds = json.loads(result.stdout)
        oauth = creds.get("claudeAiOauth")
        if not oauth:
            return None

        token = oauth.get("accessToken")
        if not token:
            return None

        # 检查是否过期
        expires_at = oauth.get("expiresAt")
        if expires_at and time.time() * 1000 > expires_at:
            logger.warning("Claude Code OAuth token 已过期，请重新执行 `claude auth login`")
            return None

        # 检查是否有推理权限
        scopes = oauth.get("scopes", [])
        if "user:inference" not in scopes:
            logger.warning("Claude Code 凭证缺少 user:inference 权限")
            return None

        return token

    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
        logger.debug("读取 Claude Code 凭证失败: %s", e)
        return None


def create_client(settings: VideoPipelineSettings) -> anthropic.Anthropic:
    """创建 Anthropic 客户端，自动解析可用凭证。

    解析顺序:
      1. settings.claude_api_key（显式配置）
      2. ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 环境变量（SDK 自动读取）
      3. Claude Code OAuth token（Keychain）
    """
    # 方式 1: 显式 API Key
    if settings.claude_api_key:
        logger.debug("使用显式配置的 API Key")
        return anthropic.Anthropic(api_key=settings.claude_api_key)

    # 方式 2: 环境变量（让 SDK 自己解析）
    import os
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        logger.debug("使用环境变量中的凭证")
        return anthropic.Anthropic()

    # 方式 3: Claude Code OAuth
    token = read_claude_code_token()
    if token:
        logger.debug("使用 Claude Code OAuth 凭证")
        return anthropic.Anthropic(
            auth_token=token,
            default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
        )

    raise AuthError(
        "找不到可用的 Claude 凭证。请任选一种方式:\n"
        "  1. 执行 `claude auth login`（推荐，无需 API Key）\n"
        "  2. 设置环境变量 ANTHROPIC_API_KEY\n"
        "  3. 在 .env 中配置 XHS_VIDEO_CLAUDE_API_KEY"
    )


def call_structured(
    client: anthropic.Anthropic,
    model: str,
    messages: list[dict[str, Any]],
    json_schema: dict[str, Any],
    schema_name: str = "response",
    *,
    system: Optional[str] = None,
    effort: str = "high",
    max_tokens: int = 16000,
) -> dict[str, Any]:
    """调用 Claude API 并获取结构化 JSON 输出。

    使用 output_config.format 确保响应严格匹配 JSON Schema，
    无需手动解析或处理格式错误。
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": effort,
            "format": {
                "type": "json_schema",
                "schema": json_schema,
            },
        },
    }

    if system:
        kwargs["system"] = system

    try:
        response = client.messages.create(**kwargs)
    except anthropic.BadRequestError as e:
        logger.error("API 请求格式错误: %s", e.message)
        raise
    except anthropic.AuthenticationError:
        logger.error("API 认证失败: 请执行 `claude auth login` 或设置 ANTHROPIC_API_KEY")
        raise
    except anthropic.RateLimitError as e:
        retry_after = e.response.headers.get("retry-after", "60")
        logger.warning("API 限流，建议 %ss 后重试", retry_after)
        raise
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:
            logger.error("API 服务端错误 (%d): %s", e.status_code, e.message)
        raise

    if response.stop_reason == "refusal":
        detail = ""
        if response.stop_details:
            detail = f" (类别: {response.stop_details.category})"
        raise ValueError(f"Claude 拒绝了此请求{detail}")

    json_text = None
    for block in response.content:
        if block.type == "text":
            json_text = block.text
            break

    if not json_text:
        raise ValueError("响应中没有文本内容")

    result = json.loads(json_text)

    logger.debug(
        "Claude API 完成: model=%s, in=%d, out=%d",
        model, response.usage.input_tokens, response.usage.output_tokens,
    )
    return result


def call_text(
    client: anthropic.Anthropic,
    model: str,
    messages: list[dict[str, Any]],
    *,
    system: Optional[str] = None,
    effort: str = "high",
    max_tokens: int = 16000,
) -> str:
    """调用 Claude API 获取纯文本响应。"""
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        raise ValueError("Claude 拒绝了此请求")

    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
