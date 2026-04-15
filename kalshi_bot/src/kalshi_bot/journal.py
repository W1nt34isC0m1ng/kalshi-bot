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
        "order_id",
        "filled_count",
    ]

    def __init__(self, filepath: str = "logs/trade_journal.csv"):
        self.filepath = Path(filepath)
        self._enabled = True

        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logging.warning("journal: could not create log directory %s: %s", self.filepath.parent, exc)
            self._enabled = False

        if self._enabled:
            self._ensure_schema()

        self._queue: queue.Queue = queue.Queue()
        self._writer_thread: threading.Thread | None = None

        if self._enabled:
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True, name="journal-writer"
            )
            self._writer_thread.start()

        # Write header if file is new/empty
        if self._enabled and (not self.filepath.exists() or self.filepath.stat().st_size == 0):
            self._queue.put("__header__")

    def _ensure_schema(self) -> None:
        """Upgrade an existing CSV file to the current header schema."""
        if not self.filepath.exists() or self.filepath.stat().st_size == 0:
            return

        try:
            with self.filepath.open("r", newline="") as f:
                reader = csv.DictReader(f)
                existing_fields = reader.fieldnames or []
                if existing_fields == self._FIELDNAMES:
                    return
                rows = list(reader)

            with self.filepath.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self._FIELDNAMES)
                writer.writeheader()
                for row in rows:
                    upgraded = {field: row.get(field, "") for field in self._FIELDNAMES}
                    writer.writerow(upgraded)
        except OSError as exc:
            logging.warning("journal: could not upgrade CSV schema for %s: %s", self.filepath, exc)
            self._enabled = False

    def _writer_loop(self) -> None:
        try:
            with self.filepath.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self._FIELDNAMES, extrasaction="ignore")
                while True:
                    item = self._queue.get()
                    if item is _SENTINEL:
                        self._queue.task_done()
                        break
                    if item == "__header__":
                        writer.writeheader()
                    else:
                        writer.writerow(item)
                    f.flush()
                    self._queue.task_done()
        except OSError as exc:
            self._enabled = False
            logging.warning("journal: disabling CSV logging for %s: %s", self.filepath, exc)

            # Drain any queued writes so shutdown does not hang waiting on work
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._queue.task_done()
                if item is _SENTINEL:
                    break

    def log_signal(
        self,
        signal: Signal,
        status: str,
        status_reason: str = "",
        order_id: str = "",
        filled_count: str = "",
    ) -> None:
        if not self._enabled:
            return
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
            "order_id": order_id,
            "filled_count": filled_count,
        }
        self._queue.put(row)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Drain the queue and stop the writer thread gracefully."""
        if not self._enabled or self._writer_thread is None:
            return
        self._queue.put(_SENTINEL)
        self._writer_thread.join(timeout=timeout)
        if self._writer_thread.is_alive():
            logging.warning("journal: writer thread did not exit cleanly within %.1fs", timeout)
