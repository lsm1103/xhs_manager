import logging
import signal
import time
from collections.abc import Callable
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from xhs_manager.models import WorkItem
from xhs_manager.queue import claim_next, complete_work_item, fail_work_item

logger = logging.getLogger(__name__)

WorkHandler = Callable[[Session, WorkItem], Optional[str]]


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
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.poll_seconds = poll_seconds
        self._stopping = False

    def stop(self, *_args: object) -> None:
        self._stopping = True

    def run_once(self) -> bool:
        with self.session_factory() as session:
            item = claim_next(
                session,
                worker_id=self.worker_id,
                supported_steps=set(self.handlers),
            )
            session.commit()
            if item is None:
                return False
            work_item_id = item.id
            step_type = item.step_type

        handler = self.handlers[step_type]
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
        return True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self._stopping:
            handled = self.run_once()
            if not handled:
                time.sleep(self.poll_seconds)
