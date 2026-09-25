"""既定の送り先: shelter/outbox/<日時>/ に書き出す（USB での持ち出しにもなる）。"""
from __future__ import annotations

from pathlib import Path

from .base import SendResult


class FileTransport:
    name = "file"

    def __init__(self, outbox: Path, batch: str):
        self.dir = outbox / batch

    def send(self, kind: str, filename: str, content: bytes, meta: dict) -> SendResult:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / filename
        path.write_bytes(content)
        return SendResult(True, str(path), f"{len(content):,} バイトを書き出しました")
