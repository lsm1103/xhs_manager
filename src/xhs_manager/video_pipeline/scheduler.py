"""每日定时调度 —— 在指定 UTC 小时触发完整流水线。

两种部署方式:
  1. 常驻进程: `python -m xhs_manager.video_pipeline.cli schedule`
     内部循环，每分钟检查一次是否到触发时刻。适合开发/临时运行。
  2. 系统级 cron / launchd（推荐生产）:
     直接在到点时执行 `cli.py run`，本模块提供配置样例生成。

幂等保证:
  `VideoPipelineRun.run_date` 唯一约束 → 同一天重复触发只会返回已有运行，不会重跑。
"""

import logging
import signal
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class DailyScheduler:
    """常驻进程版每日调度器。"""

    def __init__(
        self,
        trigger_hour_utc: int,
        job: Callable[[], None],
        poll_seconds: int = 60,
    ) -> None:
        if not 0 <= trigger_hour_utc <= 23:
            raise ValueError(f"trigger_hour_utc 必须在 0-23，收到 {trigger_hour_utc}")
        self.trigger_hour = trigger_hour_utc
        self.job = job
        self.poll_seconds = poll_seconds
        self._last_run_date: Optional[date] = None
        self._stopping = False

    def should_fire(self, now: Optional[datetime] = None) -> bool:
        """当前时刻是否应触发：到了触发小时，且今天还没跑过。"""
        now = now or datetime.now(timezone.utc)
        if now.hour != self.trigger_hour:
            return False
        return self._last_run_date != now.date()

    def run_once_if_due(self, now: Optional[datetime] = None) -> bool:
        """到点则执行一次，返回是否执行了。"""
        now = now or datetime.now(timezone.utc)
        if not self.should_fire(now):
            return False
        logger.info("到达触发时刻 %02d:00 UTC，开始执行流水线", self.trigger_hour)
        try:
            self.job()
        except Exception:
            logger.exception("流水线执行异常")
        finally:
            # 无论成败都标记今天已触发，避免同一小时内反复重试
            self._last_run_date = now.date()
        return True

    def stop(self, *_: object) -> None:
        self._stopping = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info(
            "调度器启动，每日 %02d:00 UTC 触发（每 %ds 轮询）",
            self.trigger_hour, self.poll_seconds,
        )
        while not self._stopping:
            self.run_once_if_due()
            time.sleep(self.poll_seconds)
        logger.info("调度器已停止")


# ── 系统级调度配置生成 ─────────────────────────────────────────────


def render_crontab_line(trigger_hour_utc: int, project_dir: Path) -> str:
    """生成 crontab 行。cron 用本机时区，此处按 UTC 换算需用户自行调整。"""
    python = project_dir / ".venv" / "bin" / "python"
    log = project_dir / "data" / "video_pipeline" / "cron.log"
    return (
        f"0 {trigger_hour_utc} * * * cd {project_dir} && "
        f"{python} -m xhs_manager.video_pipeline.cli run >> {log} 2>&1"
    )


def render_launchd_plist(
    trigger_hour_local: int, project_dir: Path, label: str = "com.xhs.video-pipeline",
) -> str:
    """生成 macOS launchd plist。launchd 的 StartCalendarInterval 用本机时区。"""
    python = project_dir / ".venv" / "bin" / "python"
    log_dir = project_dir / "data" / "video_pipeline"
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>xhs_manager.video_pipeline.cli</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{project_dir}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>{trigger_hour_local}</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{log_dir}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>{log_dir}/launchd.err.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
"""
