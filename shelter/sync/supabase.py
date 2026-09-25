"""将来の送り先: Supabase（REST upsert: roster → evacuees, eei → shelter_reports）。今回はスタブ。"""
from __future__ import annotations

from .base import SendResult


class SupabaseTransport:
    name = "supabase"

    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key

    def send(self, kind: str, filename: str, content: bytes, meta: dict) -> SendResult:
        # TODO: 本部の実体が決まったら実装する（仕様書 §10・§18「本部の実体」）
        return SendResult(False, self.url or "(SUPABASE_URL 未設定)",
                          "Supabase 送信は未実装です（file か http を使ってください）")
