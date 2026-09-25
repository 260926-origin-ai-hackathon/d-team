"""受付番号のバーコード（Code 128・SVG）。

避難者のスマホに常時表示し、職員が対面で対応するときに受付カメラ（ZXing）か
USB バーコードリーダーで読む。中身は受付番号を 6 桁に揃えた数字だけ（氏名などは入れない）。
"""
from __future__ import annotations

import re

import barcode
from barcode.writer import SVGWriter

DIGITS = 6
_NUM_RE = re.compile(r"\d+")


def code_text(evacuee_id: int) -> str:
    return f"{int(evacuee_id):0{DIGITS}d}"


def parse(text: str) -> int | None:
    """読み取った文字列から受付番号を取り出す（数字以外は無視。無ければ None）。"""
    m = _NUM_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def svg(evacuee_id: int, *, module_height: float = 14.0, module_width: float = 0.33) -> bytes:
    code = barcode.get("code128", code_text(evacuee_id), writer=SVGWriter())
    return code.render(writer_options={
        "write_text": False, "module_height": module_height, "module_width": module_width,
        "quiet_zone": 3.0, "background": "white", "foreground": "black",
    })
