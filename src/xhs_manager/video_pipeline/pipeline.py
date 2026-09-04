"""视频 Pipeline 主编排器 — 串联 6 个阶段，管理流水线运行生命周期。"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id, utcnow
from xhs_manager.video_pipeline.config import VideoPipelineSettings, get_video_settings
from xhs_manager.video_pipeline.domain import PipelineStatus, StageError, next_stage
from xhs_manager.video_pipeline.models import VideoPipelineRun

logger = logging.getLogger(__name__)


class VideoPipeline:
    """每日视频生产流水线。

    用法::

        pipeline = VideoPipeline(session_factory)
        result = pipeline.run()          # 执行全部阶段
        result = pipeline.run_stage(     # 只执行某个阶段
            run_id, PipelineStatus.COLLECTING
        )
    """

    def __init__(
        self,
        session_factory,
        settings: Optional[VideoPipelineSettings] = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_video_settings()

    # ── 创建运行记录 ──────────────────────────────────────────────

    def create_run(
        self,
        run_date: Optional[date] = None,
        trigger_type: str = "scheduled",
    ) -> str:
        """创建新的流水线运行记录，返回 run_id。如果当日已有运行则返回已有的 id。"""
        today = run_date or date.today()

        with self.session_factory() as session:
            existing = (
                session.query(VideoPipelineRun)
                .filter(VideoPipelineRun.run_date == today)
                .first()
            )
            if existing:
                logger.info("当日流水线已存在: %s (%s)", existing.id, existing.status)
                return existing.id

            run = VideoPipelineRun(
                id=new_id(),
                trigger_type=trigger_type,
                run_date=today,
                status=PipelineStatus.COLLECTING.value,
                config_snapshot=self._snapshot_config(),
                started_at=utcnow(),
            )
            session.add(run)
            session.commit()
            logger.info("创建流水线运行: %s (日期=%s)", run.id, today)
            return run.id

    # ── 执行全部阶段 ──────────────────────────────────────────────

    def run(self, run_date: Optional[date] = None) -> dict[str, Any]:
        """执行完整的 6 阶段流水线。"""
        run_id = self.create_run(run_date)
        result: dict[str, Any] = {"run_id": run_id, "stages": {}}

        stage_order = [
            PipelineStatus.COLLECTING,
            PipelineStatus.SELECTING,
            PipelineStatus.MATERIALIZING,
            PipelineStatus.COMPOSING,
            PipelineStatus.RENDERING,
            PipelineStatus.PUBLISHING,
        ]

        for stage in stage_order:
            try:
                stage_result = self.run_stage(run_id, stage)
                result["stages"][stage.value] = {
                    "status": "completed",
                    "result": stage_result,
                }
                logger.info("阶段完成: %s", stage.value)
            except StageError as e:
                logger.error("阶段失败: %s — %s", stage.value, e.message)
                result["stages"][stage.value] = {
                    "status": "failed",
                    "error": e.message,
                }
                self._mark_failed(run_id, str(e))
                result["status"] = "failed"
                return result
            except Exception as e:
                logger.exception("阶段异常: %s", stage.value)
                self._mark_failed(run_id, f"[{stage.value}] {e}")
                result["stages"][stage.value] = {
                    "status": "error",
                    "error": str(e),
                }
                result["status"] = "failed"
                return result

        self._mark_completed(run_id)
        result["status"] = "completed"
        return result

    # ── 执行单个阶段 ──────────────────────────────────────────────

    def run_stage(self, run_id: str, stage: PipelineStatus) -> dict[str, Any]:
        """执行流水线的某个特定阶段。"""
        from xhs_manager.video_pipeline.stages.stage1_trends import collect_trends
        from xhs_manager.video_pipeline.stages.stage2_topics import select_topics
        from xhs_manager.video_pipeline.stages.stage3_materials import collect_materials
        from xhs_manager.video_pipeline.stages.stage4_compose import compose_html
        from xhs_manager.video_pipeline.stages.stage5_render import render_videos
        from xhs_manager.video_pipeline.stages.stage6_publish import publish_videos

        stage_handlers = {
            PipelineStatus.COLLECTING: collect_trends,
            PipelineStatus.SELECTING: select_topics,
            PipelineStatus.MATERIALIZING: collect_materials,
            PipelineStatus.COMPOSING: compose_html,
            PipelineStatus.RENDERING: render_videos,
            PipelineStatus.PUBLISHING: publish_videos,
        }

        handler = stage_handlers.get(stage)
        if not handler:
            raise StageError(stage.value, f"未知的阶段: {stage.value}")

        # 更新运行状态
        self._update_status(run_id, stage)

        # 执行阶段
        with self.session_factory() as session:
            run = session.get(VideoPipelineRun, run_id)
            if not run:
                raise StageError(stage.value, f"流水线运行不存在: {run_id}")

            result = handler(session, run, self.settings)
            session.commit()

        # 推进到下一阶段
        next_st = next_stage(stage)
        if next_st != PipelineStatus.COMPLETED:
            self._update_status(run_id, next_st)

        return result

    # ── 内部方法 ──────────────────────────────────────────────────

    def _update_status(self, run_id: str, status: PipelineStatus) -> None:
        with self.session_factory() as session:
            run = session.get(VideoPipelineRun, run_id)
            if run:
                run.status = status.value
                session.commit()

    def _mark_completed(self, run_id: str) -> None:
        with self.session_factory() as session:
            run = session.get(VideoPipelineRun, run_id)
            if run:
                run.status = PipelineStatus.COMPLETED.value
                run.completed_at = utcnow()
                session.commit()
        logger.info("流水线完成: %s", run_id)

    def _mark_failed(self, run_id: str, error: str) -> None:
        with self.session_factory() as session:
            run = session.get(VideoPipelineRun, run_id)
            if run:
                run.status = PipelineStatus.FAILED.value
                run.error_detail = error[:2000]
                run.completed_at = utcnow()
                session.commit()
        logger.error("流水线失败: %s — %s", run_id, error[:200])

    def _snapshot_config(self) -> dict[str, Any]:
        """快照当前配置，便于审计和回溯。"""
        return {
            "topics_per_run": self.settings.topics_per_run,
            "trend_platforms": self.settings.trend_platforms,
            "trends_per_platform": self.settings.trends_per_platform,
            "trend_keywords": self.settings.trend_keywords,
            "max_duration": self.settings.max_duration,
            "min_duration": self.settings.min_duration,
            "claude_model": self.settings.claude_model,
            "tts_provider": self.settings.tts_provider,
            "tts_voice": self.settings.tts_voice,
            "render_fps": self.settings.render_fps,
            "render_resolution": self.settings.render_resolution,
            "publish_platforms": self.settings.publish_platforms,
        }
