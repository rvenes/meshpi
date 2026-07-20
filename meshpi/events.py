from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class EventHub:
    def __init__(self, queue_size: int = 200):
        self.queue_size = queue_size
        self._queues: set[queue.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = tuple(self._queues)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    @contextmanager
    def subscribe(self) -> Iterator[queue.Queue[dict[str, Any]]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(self.queue_size)
        with self._lock:
            self._queues.add(subscriber)
        try:
            yield subscriber
        finally:
            with self._lock:
                self._queues.discard(subscriber)

