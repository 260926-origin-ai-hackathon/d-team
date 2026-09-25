"""本部同期の送り先（Transport）の共通の形。送り先が増えても送るもの（名簿 CSV・EEI JSON）は変えない。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SendResult:
    ok: bool
    target: str
    detail: str = ""
    extra: dict = field(default_factory=dict)


class Transport(Protocol):
    name: str

    def send(self, kind: str, filename: str, content: bytes, meta: dict) -> SendResult: ...
