#!/usr/bin/env python3
"""app/index.html（正）から、公開用 index.html と 単体版 を生成する。

  python3 data/sync_html.py          # 生成して書き込む
  python3 data/sync_html.py --check  # 書き込まず、現状ファイルと一致するか確認する

なぜ単純コピーではダメか：
  - app/index.html は app/ 配下にあるため、manifest/アイコンを「../」で参照している。
    ルート index.html にそのままコピーすると公開URLで 404 になる（../ がリポジトリの外を指す）。
  - 単体版は「単一ファイルで完結（file://で直接開ける）」が設計要件なので、
    外部ファイル参照（manifest・icons/）を持たせず、apple-touch-icon だけ base64 で埋め込む。

questions.js のインライン化（<script src="questions.js"> → 中身を直接埋め込み）は両方に共通。
"""
import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app" / "index.html"
QJS = ROOT / "app" / "questions.js"
ICON = ROOT / "icons" / "apple-touch-icon.png"
OUT_ROOT = ROOT / "index.html"
OUT_STANDALONE = ROOT / "NARUTO検定_単体版.html"

SRC_TAG = '<script src="questions.js"></script>'


def inline_questions(html: str) -> str:
    if html.count(SRC_TAG) != 1:
        sys.exit(f"ERROR: {SRC_TAG} が app/index.html に {html.count(SRC_TAG)} 個ある（1個のはず）")
    q = QJS.read_text(encoding="utf-8").rstrip("\n")
    return html.replace(SRC_TAG, "<script>\n" + q + "\n</script>")


def build_root(base: str) -> str:
    out = base.replace('href="../manifest.webmanifest"', 'href="manifest.webmanifest"')
    out = out.replace('href="../icons/', 'href="icons/')
    if "../" in re.sub(r"<script>.*?</script>", "", out, flags=re.S).split("</head>")[0]:
        sys.exit("ERROR: ルート版の<head>に ../ が残っている")
    return out


def build_standalone(base: str) -> str:
    lines = base.split("\n")
    kept = []
    for ln in lines:
        if 'rel="manifest"' in ln:
            continue
        if 'rel="icon"' in ln and "icons/" in ln:
            continue
        if 'name="apple-mobile-web-app-title"' in ln:  # 単体版は元々持たない（既存仕様を維持）
            continue
        if 'rel="apple-touch-icon"' in ln:
            b64 = base64.b64encode(ICON.read_bytes()).decode("ascii")
            ln = f'<link rel="apple-touch-icon" sizes="180x180" href="data:image/png;base64,{b64}">'
        kept.append(ln)
    return "\n".join(kept)


def main() -> None:
    check = "--check" in sys.argv
    base = inline_questions(APP.read_text(encoding="utf-8"))
    targets = {OUT_ROOT: build_root(base), OUT_STANDALONE: build_standalone(base)}
    ok = True
    for path, content in targets.items():
        if check:
            same = path.exists() and path.read_text(encoding="utf-8") == content
            print(("一致  " if same else "不一致") + f"  {path.name}")
            ok &= same
        else:
            path.write_text(content, encoding="utf-8")
            print(f"書き込み  {path.name}  ({len(content.encode()) / 1024:.0f}KB)")
    if check and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
