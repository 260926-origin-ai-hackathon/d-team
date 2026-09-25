"""避難所AIに同梱する公開データ（内閣府・大阪市）を取得し、PDF をテキスト化する。

    python scripts/fetch_public_data.py            # 無いものだけ取得
    python scripts/fetch_public_data.py --force    # 全部取り直す

取得先と利用条件は shelter/data/README.md を参照。
"""
from __future__ import annotations

import logging
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "shelter" / "data"
UA = {"User-Agent": "Mozilla/5.0 (local-ai-hackathon shelter data fetch)"}

OSAKA = "https://www.city.osaka.lg.jp"
G = f"{OSAKA}/kikikanrishitsu/cmsfiles/contents/0000474/474277"
H = f"{OSAKA}/kikikanrishitsu/cmsfiles/contents/0000300/300781"
E = f"{OSAKA}/kikikanrishitsu/cmsfiles/contents/0000522/522880"
M = f"{OSAKA}/miyakojima/cmsfiles/contents/0000002/2475"

FILES: dict[str, str] = {
    # 内閣府
    "manual/2412hinanjo_guideline.pdf": "https://www.bousai.go.jp/taisaku/hinanjo/pdf/2412hinanjo_guideline.pdf",
    "manual/2412kankyokakuho.pdf": "https://www.bousai.go.jp/taisaku/hinanjo/pdf/2412kankyokakuho.pdf",
    "manual/1604hinanjo_toilet_guideline.pdf": "https://www.bousai.go.jp/taisaku/hinanjo/pdf/1604hinanjo_toilet_guideline.pdf",
    # 大阪市 避難所開設・運営ガイドライン（令和7年3月改訂）
    "manual/osaka_hinanjo_guideline_honpen_R7.pdf": f"{G}/honpen.pdf",
    "manual/osaka_hinanjo_guideline_shiryo_R7.pdf": f"{G}/information.pdf",
    "manual/osaka_hinanjo_guideline_youshiki_R7.xlsx": f"{G}/youshiki.xlsx",
    "manual/osaka_pet_guide.pdf": f"{G}/pet_guide.pdf",
    "manual/osaka_pet_manual.pdf": f"{G}/pet_manual.pdf",
    # 応急手当・要配慮者
    "manual/saitama_seibu_kyumei3.pdf": "http://www.saisei119.jp/_res/projects/default_project/_page_/001/001/207/kyumei3.pdf",
    "manual/mhlw_shougaiji_hairyo_R1.pdf": "https://www.mhlw.go.jp/content/10200000/000557243.pdf",
    # 都島区 水害ハザードマップ（日・英）
    "hazard/miyakojima_suigai_keihatsu_ja.pdf": f"{H}/02miyakojimaku(keihatu)hp.pdf",
    "hazard/miyakojima_suigai_chizu_ja.pdf": f"{H}/2miyakojimaku_chizumen202107.pdf",
    "hazard/miyakojima_suigai_chusho_ja.pdf": f"{H}/2miyakojima(tyuusyo).pdf",
    "hazard/miyakojima_suigai_keihatsu_en.pdf": f"{E}/02miyakojimaku(keihatu)hp_EN.pdf",
    "hazard/miyakojima_suigai_chizu_en.pdf": f"{E}/2miyakojima(E)_chizumen202107.pdf",
    "hazard/miyakojima_suigai_chusho_en.pdf": f"{E}/(E)2miyakojima(tyuusyou)EN.pdf",
    "hazard/miyakojima_yodogawa_202107.png": f"{H}/yodogawa_202107.png",
    "hazard/miyakojima_neyagawa_202107.png": f"{H}/neyagawa_202107.png",
    "hazard/miyakojima_miyakojima_kyuuyodogawaryuuikitou.png": f"{H}/miyakojima_kyuuyodogawaryuuikitou.png",
    "hazard/miyakojima_takashio_202107.png": f"{H}/takashio_202107.png",
    "hazard/miyakojima_naisui_202107.png": f"{H}/naisui_202107.png",
    "hazard/miyakojima_nankaitorafu_202107.png": f"{H}/nankaitorafu_202107.png",
    "hazard/miyakojima_hannrei2107.png": f"{H}/hannrei2107.png",
    # 都島区 防災マップ・避難所一覧（2026年3月）
    "local/miyakojima_shelter_list_2026.pdf": f"{M}/2026shelter.pdf",
    "local/miyakojima_shelter_list_2026.jpg": f"{M}/20261shelter.jpg",
    "local/miyakojima_bousai_map_2026_ja.pdf": f"{M}/mdpm2026_jp.pdf",
    "local/miyakojima_bousai_map_2026_en.pdf": f"{M}/mdpm2026_en.pdf",
}


def fetch(rel: str, url: str, force: bool) -> Path:
    dst = DATA / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        print(f"  skip  {rel}")
        return dst
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        f.write(r.read())
    print(f"  ok    {rel} ({dst.stat().st_size:,} bytes)")
    return dst


def extract_text(pdf: Path) -> None:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("  pypdf が無いのでテキスト化は省略（pip install pypdf）")
        return
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    out = pdf.with_suffix(".txt")
    pages = [pg.extract_text() or "" for pg in PdfReader(str(pdf)).pages]
    out.write_text("\n\n".join(f"<<page {i + 1}>>\n{t}" for i, t in enumerate(pages)), encoding="utf-8")
    print(f"  text  {out.relative_to(DATA)} ({sum(map(len, pages)):,} chars)")


def main() -> int:
    force = "--force" in sys.argv
    print(f"保存先: {DATA}")
    for rel, url in FILES.items():
        try:
            p = fetch(rel, url, force)
        except Exception as e:  # noqa: BLE001
            print(f"  NG    {rel}: {e}")
            continue
        if p.suffix.lower() == ".pdf" and (force or not p.with_suffix(".txt").exists()):
            extract_text(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
