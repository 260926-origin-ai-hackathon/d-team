"""運営マニュアル（shelter/data/manual/*.txt|*.md）を埋め込んで SQLite に入れる。

    python scripts/ingest_manual.py            # 取り込み直す
    python scripts/ingest_manual.py --search "要介護の方の夜間の対応"   # 検索だけ試す
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shelter import db, rag  # noqa: E402
from shelter.config import config  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", help="取り込まずに検索だけする")
    args = ap.parse_args()
    db.init_db()

    if args.search:
        for h in await rag.search(args.search):
            print(f"[{h['score']}] {h['title']} › {h['section']}\n  {h['text'][:160]}\n")
        return 0

    files = rag.manual_files()
    total = sum(len(rag.chunk_file(f)) for f in files)
    print(f"{len(files)} ファイル / {total} チャンク を {config.embed_model} で埋め込みます")
    t0 = time.perf_counter()
    task = asyncio.create_task(rag.ingest())
    while not task.done():
        st = rag.ingest_state()
        print(f"\r  {st['done']}/{st['total']} {st['message']}   ", end="", flush=True)
        await asyncio.sleep(1)
    st = task.result()
    print(f"\n{st['message']}（{time.perf_counter() - t0:.0f} 秒）")
    return 0 if st["chunks"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
