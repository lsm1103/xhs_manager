import logging
import signal
import threading
import time
from collections.abc import Callable
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from xhs_manager.domain import ConflictError
from xhs_manager.models import WorkItem
from xhs_manager.queue import claim_next, complete_work_item, fail_work_item, renew_lease

logger = logging.getLogger(__name__)

WorkHandler = Callable[[Session, WorkItem], Optional[str]]

# 租约默认 60 秒。心跳每隔它的三分之一续一次，留足重试余量。
DEFAULT_LEASE_SECONDS = 60


class RetryableStepError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker,
        handlers: dict[str, WorkHandler],
        *,
        worker_id: Optional[str] = None,
        poll_seconds: float = 2.0,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        step_lease_seconds: Optional[dict[str, int]] = None,
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        # 某些步骤天生就久（渲染一支 2 分半的片子约 4 分钟），
        # 它们需要更长的租约，否则心跳一断立刻就会被别的 worker 抢走。
        self.step_lease_seconds = step_lease_seconds or {}
        self._stopping = False

    def _lease_for(self, step_type: str) -> int:
        return self.step_lease_seconds.get(step_type, self.lease_seconds)

    def stop(self, *_args: object) -> None:
        self._stopping = True

    def run_once(self) -> bool:
        with self.session_factory() as session:
            item = claim_next(
                session,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                supported_steps=set(self.handlers),
            )
            session.commit()
            if item is None:
                return False
            work_item_id = item.id
            step_type = item.step_type

        handler = self.handlers[step_type]
        lease = self._lease_for(step_type)

        # 认领时用的是通用租约（认领前还不知道是哪个步骤），
        # 领到之后立刻按步骤本身的时长把租约升上去——
        # 否则渲染这种长步骤会在第一次心跳之前就过期。
        if lease != self.lease_seconds:
            self._renew(work_item_id, lease)

        # 心跳：处理器跑多久就续多久租约。
        # 没有它的话，任何超过租约时长的步骤都会被另一个 worker 当成
        # 「租约过期」重新领走——同一支片子渲染两遍。
        heartbeat = self._start_heartbeat(work_item_id, lease)
        try:
            with self.session_factory() as session:
                item = session.get(WorkItem, work_item_id)
                output_ref = handler(session, item)
                complete_work_item(
                    session,
                    work_item_id=work_item_id,
                    worker_id=self.worker_id,
                    output_ref=output_ref,
                )
                session.commit()
        except RetryableStepError as exc:
            with self.session_factory() as session:
                fail_work_item(
                    session,
                    work_item_id=work_item_id,
                    worker_id=self.worker_id,
                    error_code=exc.code,
                    error_detail=exc.message,
                    retryable=True,
                )
                session.commit()
        except Exception as exc:
            logger.exception("工作项执行失败", extra={"work_item_id": work_item_id})
            with self.session_factory() as session:
                fail_work_item(
                    session,
                    work_item_id=work_item_id,
                    worker_id=self.worker_id,
                    error_code="UNHANDLED_STEP_ERROR",
                    error_detail=str(exc),
                    retryable=False,
                )
                session.commit()
        finally:
            heartbeat.set()
        return True

    def _renew(self, work_item_id: str, lease_seconds: int) -> bool:
        """续一次租约。成功返回 True。"""
        with self.session_factory() as session:
            renew_lease(
                session,
                work_item_id=work_item_id,
                worker_id=self.worker_id,
                lease_seconds=lease_seconds,
            )
            session.commit()
        return True

    def _start_heartbeat(self, work_item_id: str, lease_seconds: int) -> threading.Event:
        """后台续租，直到返回的 Event 被 set。

        心跳间隔不只看步骤租约，还要顾及**当前**这条租约什么时候到期：
        续期周期必须短于两者中较小的那个，否则第一次续期之前租约就凉了。
        """
        stop = threading.Event()
        interval = max(1.0, min(lease_seconds, self.lease_seconds) / 3)

        def beat() -> None:
            misses = 0
            while not stop.wait(interval):
                try:
                    self._renew(work_item_id, lease_seconds)
                    misses = 0
                except ConflictError:
                    # 租约真的丢了（被别人接管），继续续没有意义
                    logger.warning("租约已被接管，停止心跳: %s", work_item_id)
                    return
                except Exception as exc:
                    # SQLite 在长写事务期间会短暂锁库，这类是瞬时错误，
                    # 不该让心跳永久停摆——连续失败到租约快没了才放弃。
                    misses += 1
                    logger.warning("续租失败（第 %d 次）: %s", misses, exc)
                    if misses >= 3:
                        logger.error("连续续租失败，停止心跳: %s", work_item_id)
                        return

        threading.Thread(target=beat, name=f"lease-{work_item_id[:8]}", daemon=True).start()
        return stop

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self._stopping:
            handled = self.run_once()
            if not handled:
                time.sleep(self.poll_seconds)
