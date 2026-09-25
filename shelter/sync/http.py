"""HTTP の送り先: SYNC_URL に multipart/form-data で POST（Authorization: Bearer SYNC_TOKEN）。

本部側は何でもよい（受け口の例: フィールド file=ファイル本体, kind=roster|eei, shelter=避難所名, as_of=時刻）。
"""
from __future__ import annotations

import httpx

from .base import SendResult


class HttpTransport:
    name = "http"

    def __init__(self, url: str, token: str = "", timeout: float = 20.0):
        if not url:
            raise ValueError("送り先 URL（SYNC_URL）が未設定です")
        self.url = url
        self.token = token
        self.timeout = timeout

    def send(self, kind: str, filename: str, content: bytes, meta: dict) -> SendResult:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        ctype = "text/csv" if filename.endswith(".csv") else "application/json"
        try:
            r = httpx.post(
                self.url,
                headers=headers,
                data={"kind": kind, **{k: str(v) for k, v in meta.items()}},
                files={"file": (filename, content, ctype)},
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            return SendResult(False, self.url, f"接続できません: {type(e).__name__}: {e}")
        ok = 200 <= r.status_code < 300
        return SendResult(ok, self.url, f"HTTP {r.status_code} {r.text[:200]}")
