from __future__ import annotations

import csv
import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Signal

_SENTINEL = None  # signals the writer thread to flush and exit


class TradeJournal:
    """Thread-safe CSV journal.

    All writes go through an internal queue consumed by a single background
    writer thread, eliminating the possibility of interleaved rows when the
    main loop and any future async callers write simultaneously.
    """

    _FIELDNAMES = [
        "ts_utc",
        "ticker",
        "side",
        "price",
        "edge_cents",
        "spread_cents",
        "score",
        "reason",
        "status",
        "status_reason",
    ]

    def __init__(self, filepath: str = "logs/trade_journal.csv"):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        self._queue: queue.Queue = queue.Queue()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True, name="journal-writer"
        )
        self._writer_thread.start()

        # Write header if file is new/empty
        if not self.filepath.exists() or self.filepath.stat().st_size == 0:
            self._queue.put("__header__")

    def _writer_loop(self) -> None:
        with self.filepath.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self._FIELDNAMES)
            while True:
                item = self._queue.get()
                if item is _SENTINEL:
                    break
                if item == "__header__":
                    writer.writeheader()
                else:
                    writer.writerow(item)
                f.flush()
                self._queue.task_done()

    def log_signal(self, signal: Signal, status: str, status_reason: str = "") -> None:
        row = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "ticker": signal.ticker,
            "side": signal.side,
            "price": signal.price,
            "edge_cents": signal.edge_cents,
            "spread_cents": signal.spread_cents,
            "score": signal.score,
            "reason": signal.reason,
            "status": status,
            "status_reason": status_reason,
        }
        self._queue.put(row)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Drain the queue and stop the writer thread gracefully."""
        self._queue.put(_SENTINEL)
        self._writer_thread.join(timeout=timeout)
        if self._writer_thread.is_alive():
            logging.warning("journal: writer thread did not exit cleanly within %.1fs", timeout)
