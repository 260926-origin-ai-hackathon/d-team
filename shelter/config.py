"""避難所AIの設定。.env → 環境変数 → 既定値 の順（localai と同じ .env を読む）。

画面から変えられる項目（避難所名・SSID など）は DB の settings テーブルが優先。
ここは起動時の既定値と、画面から変えない項目（PIN・同期トークン等）を持つ。
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from localai import settings as ai_settings  # .env の読み込みも兼ねる

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent
DATA_DIR = PKG_DIR / "data"

JST = timezone(timedelta(hours=9))


def now() -> datetime:
    return datetime.now(JST).replace(microsecond=0)


def now_iso() -> str:
    return now().isoformat()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass
class Config:
    # 避難所（画面の「設定」で上書きできる）
    name: str = _env("SHELTER_NAME", "東高等学校 避難所")
    address: str = _env("SHELTER_ADDRESS", "大阪市都島区東野田町4-15-14")
    host_ip: str = _env("SHELTER_HOST_IP", "192.168.137.1")
    port: int = int(_env("SHELTER_PORT", "8000"))
    ssid: str = _env("SHELTER_SSID", "HINANJO-AI")
    wifi_password: str = _env("SHELTER_WIFI_PASSWORD", "")
    # 想定地区（この避難所に来ると想定している町名。カンマ区切り。英語の住所も拾うためローマ字も並べる）
    area_towns: str = _env("SHELTER_AREA_TOWNS", "東野田町,Higashinoda")

    # 運営者画面
    staff_pin: str = _env("SHELTER_STAFF_PIN", "1234")
    secret_key: str = _env("SHELTER_SECRET_KEY", "")
    session_hours: int = 12

    # AI（チャットモデルは localai の設定＝LOCALAI_MODEL を使う）
    chat_model: str = _env("SHELTER_CHAT_MODEL", "") or ai_settings.model
    embed_model: str = _env("SHELTER_EMBED_MODEL", "bge-m3")
    ollama_host: str = ai_settings.host
    num_ctx: int = int(_env("SHELTER_NUM_CTX", "8192"))
    temperature: float = float(_env("SHELTER_TEMPERATURE", "0.2"))
    max_tokens_evacuee: int = int(_env("SHELTER_MAX_TOKENS_EVACUEE", "300"))
    max_tokens_plan: int = int(_env("SHELTER_MAX_TOKENS_PLAN", "80"))  # 避難者チャットの「考え」の上限
    max_tokens_staff: int = int(_env("SHELTER_MAX_TOKENS_STAFF", "600"))
    warmup: bool = _env("SHELTER_WARMUP", "1") != "0"
    # 接続時にページを自動で開く（キャプティブポータル。host_ip の 53/80 番を使う。captive.py）
    # 既定は OFF（v1.5）。機種によって出方が違い、サインイン画面は Safari 等と保存領域が別になりうるため、
    # 入口ポスターは Wi-Fi と URL の QR 2枚で案内する。試すときだけ SHELTER_CAPTIVE=1
    captive: bool = _env("SHELTER_CAPTIVE", "0") == "1"

    # クラウド AI（Gemini）。キーがあるときだけ画面に切り替えトグルが出る。埋め込みは常に Ollama
    gemini_api_key: str = _env("GEMINI_API_KEY")
    gemini_model: str = _env("GEMINI_MODEL", "gemini-3.8-flash")
    gemini_thinking_budget: int = int(_env("GEMINI_THINKING_BUDGET", "0"))

    # 保存先
    db_path: Path = field(
        default_factory=lambda: Path(_env("SHELTER_DB", str(DATA_DIR / "shelter.db")))
    )
    outbox_dir: Path = field(
        default_factory=lambda: Path(_env("SHELTER_OUTBOX", str(PKG_DIR / "outbox")))
    )

    # 本部同期
    sync_transport: str = _env("SYNC_TRANSPORT", "file")
    sync_url: str = _env("SYNC_URL", "")
    sync_token: str = _env("SYNC_TOKEN", "")
    supabase_url: str = _env("SUPABASE_URL", "")
    supabase_key: str = _env("SUPABASE_KEY", "")

    def __post_init__(self) -> None:
        if not self.secret_key:
            # 未設定なら起動ごとに作る（再起動で運営者は再ログイン）
            self.secret_key = secrets.token_hex(32)

    @property
    def entry_url(self) -> str:
        return f"http://{self.host_ip}:{self.port}/"


config = Config()
