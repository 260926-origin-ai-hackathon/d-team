"""個人側アプリ担当向け: 受付 QR（仕様書 §6.1 のペイロード）のサンプル画像を作る。

    python scripts/make_sample_qr.py                 # outputs/sample_checkin_*.png を3枚
    python scripts/make_sample_qr.py --compressed    # 600 バイト超え用の z: 形式も作る

PC 画面や印刷物をカメラにかざして、受付（/staff/checkin）の QR 読み取りを試せる。
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import zlib
from pathlib import Path

import qrcode

SAMPLES = [
    {"name": "山田 太郎", "kana": "やまだ たろう", "addr": "大阪市都島区東野田町1-2-3-405", "sex": "M",
     "dob": "1958-04-02", "hh": 3, "care": ["ILL", "MED"], "alg": [], "note": "高血圧の薬を毎朝",
     "lang": "ja", "pet": 0},
    {"name": "佐藤 花子", "kana": "さとう はなこ", "addr": "大阪市都島区東野田町4-1-8", "sex": "F",
     "dob": "1994-11-20", "hh": 2, "care": ["INF", "ALG"], "alg": ["egg", "milk"],
     "note": "8か月の子ども連れ。ミルクが必要", "lang": "ja", "pet": 1},
    {"name": "Emily Clark", "kana": "", "addr": "Osaka, Miyakojima-ku, Higashinoda 2-5-1", "sex": "F",
     "dob": "1990-06-15", "hh": 1, "care": [], "alg": ["peanut"], "note": "Japanese: a little",
     "lang": "en", "pet": 0},
]


def payload(sample: dict, dev: str) -> dict:
    return {"v": 1, "fmt": "hinanjo-checkin", "dev": dev, **sample}


def encode(p: dict, compressed: bool) -> str:
    text = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    if compressed or len(text.encode("utf-8")) > 600:
        return "z:" + base64.urlsafe_b64encode(zlib.compress(text.encode("utf-8"))).decode().rstrip("=")
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--compressed", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(SAMPLES, 1):
        p = payload(s, secrets.token_hex(8))
        data = encode(p, args.compressed)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
        qr.add_data(data.encode("utf-8"))
        qr.make(fit=True)
        path = out / f"sample_checkin_{i}{'_z' if args.compressed else ''}.png"
        qr.make_image(fill_color="black", back_color="white").save(path)
        (out / f"sample_checkin_{i}.json").write_text(json.dumps(p, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
        print(f"{path}  ({len(data.encode('utf-8'))} bytes, QR version {qr.version})  {s['name']}")


if __name__ == "__main__":
    main()
