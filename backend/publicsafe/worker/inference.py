"""The single inference thread: pulls from the Scheduler, runs the Pipeline (E.3)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from publicsafe.core.mailbox import Scheduler
from publicsafe.core.models import Frame, FrameResult
from publicsafe.core.timing import now_ms
from publicsafe.worker.pipeline import DecodeError, Pipeline

log = logging.getLogger("publicsafe.worker")

OnResult = Callable[[Frame, FrameResult], None]
OnError = Callable[[Frame, BaseException], None]


class InferenceWorker(threading.Thread):
    def __init__(
        self,
        scheduler: Scheduler,
        pipeline: Pipeline,
        on_result: OnResult,
        on_error: OnError,
        poll_s: float = 0.25,
    ) -> None:
        super().__init__(name="inference", daemon=True)
        self.scheduler = scheduler
        self.pipeline = pipeline
        self.on_result = on_result
        self.on_error = on_error
        self.poll_s = poll_s
        self._stop_event = threading.Event()
        self.processed = 0
        self.errors = 0

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout)

    def run(self) -> None:
        while not self._stop_event.is_set():
            frame = self.scheduler.next(timeout=self.poll_s)
            if frame is None:
                continue
            frame.t_dequeued_ms = now_ms()
            try:
                result = self.pipeline.process(frame)
            except DecodeError as exc:
                self.errors += 1
                self.on_error(frame, exc)
            except Exception as exc:
                self.errors += 1
                log.exception(
                    "frame processing failed camera=%s seq=%s", frame.camera_id, frame.seq
                )
                self.on_error(frame, exc)
            else:
                self.processed += 1
                self.on_result(frame, result)
