"""审计日志：JSONL，逐条落盘，不含正文与凭据。"""

import json
from pathlib import Path
from typing import TextIO

from .redaction import sanitize_text

_TEXT_FIELDS = ("subject_label", "reason", "failure", "abort_reason", "message")


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO = self.path.open("w", encoding="utf-8")

    def write(self, entry: dict) -> None:
        safe = {}
        for key, value in entry.items():
            if isinstance(value, str) and key in _TEXT_FIELDS:
                safe[key] = sanitize_text(value, 200)
            else:
                safe[key] = value
        self._handle.write(json.dumps(safe, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
