"""Claude 客户端 —— 通过 Claude Code headless 模式（`claude -p`）调用。

为什么不用 Anthropic SDK 直连:
  Claude Code 的 OAuth token 是给 Claude Code 自己用的。
  拿它直接打 API 端点会被当作异常用法持续 429（返回的 message 只有 "Error"）。
  正确做法是让 Claude Code 自己发请求 —— 走 Max 订阅通道，无需 API Key，无限流问题。

能力:
  - `--json-schema` 原生结构化输出，返回已解析的 dict
  - `--system-prompt` 系统提示
  - `--model` 指定模型
  - `--tools ""` 禁用工具（纯文本生成场景不需要）
  - `--no-session-persistence` 不污染会话历史
"""

import json
import logging
import shutil
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ClaudeCliError(Exception):
    """claude CLI 调用失败。"""


def available() -> bool:
    """claude CLI 是否可用。"""
    return shutil.which("claude") is not None


def call_structured(
    prompt: str,
    json_schema: dict[str, Any],
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """调用 Claude 并获取严格匹配 schema 的结构化输出。

    Returns:
        已解析的 dict（来自 CLI 响应的 `structured_output` 字段）
    Raises:
        ClaudeCliError: CLI 不可用 / 执行失败 / 响应格式异常
    """
    if not available():
        raise ClaudeCliError("找不到 claude 命令，请确认 Claude Code 已安装且在 PATH 中")

    cmd = [
        "claude", "-p", prompt,
        "--json-schema", json.dumps(json_schema, ensure_ascii=False),
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
    ]
    if system:
        cmd += ["--system-prompt", system]
    if model:
        cmd += ["--model", model]

    payload = _run(cmd, timeout)

    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        return structured

    # 兜底：某些版本只在 result 里给 JSON 字符串
    result = payload.get("result")
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ClaudeCliError(
        f"响应中没有可用的结构化输出。result 前 200 字: {str(result)[:200]}"
    )


def call_text(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = 300,
) -> str:
    """调用 Claude 获取纯文本响应。"""
    if not available():
        raise ClaudeCliError("找不到 claude 命令")

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
    ]
    if system:
        cmd += ["--system-prompt", system]
    if model:
        cmd += ["--model", model]

    payload = _run(cmd, timeout)
    result = payload.get("result")
    if not isinstance(result, str):
        raise ClaudeCliError("响应中没有文本内容")
    return result


def _run(cmd: list[str], timeout: int) -> dict[str, Any]:
    """执行 claude CLI，返回解析后的 JSON 响应。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeCliError(f"claude 调用超时 ({timeout}s)") from e
    except FileNotFoundError as e:
        raise ClaudeCliError("找不到 claude 命令") from e

    if proc.returncode != 0:
        raise ClaudeCliError(
            f"claude 退出码 {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeCliError(f"claude 输出不是 JSON: {proc.stdout[:300]}") from e

    if payload.get("is_error"):
        raise ClaudeCliError(f"claude 返回错误: {payload.get('result', '')[:500]}")

    usage = payload.get("usage", {})
    logger.debug(
        "claude 调用完成: model=%s in=%s out=%s cost=$%.4f",
        list(payload.get("modelUsage", {}).keys()),
        usage.get("input_tokens"), usage.get("output_tokens"),
        payload.get("total_cost_usd", 0),
    )
    return payload
