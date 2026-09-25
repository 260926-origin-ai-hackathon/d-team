"""入口 QR（URL）と Wi-Fi 接続 QR の PNG を作る（印刷用）。

    python scripts/make_entry_qr.py                     # .env / 設定画面の値で作る
    python scripts/make_entry_qr.py --ip 192.168.137.1 --out outputs/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import qrcode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shelter import db  # noqa: E402
from shelter.config import config  # noqa: E402
from shelter.main import wifi_qr_text  # noqa: E402


def save(data: str, path: Path) -> None:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=14, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(path)
    print(f"{path}  ← {data}")


def main() -> int:
    db.init_db()
    p = db.profile()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=p["host_ip"])
    ap.add_argument("--ssid", default=p["ssid"])
    ap.add_argument("--password", default=p["wifi_password"] or "")
    ap.add_argument("--out", default="outputs")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    save(f"http://{args.ip}:{config.port}/", out / "entry_qr.png")
    save(wifi_qr_text(args.ssid, args.password), out / "wifi_qr.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
