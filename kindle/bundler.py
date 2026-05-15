#!/usr/bin/env python3
"""
Kindle Monthly Bundler
毎月1日に実行し、先月のZenn記事とnote記事を束ねてKindle出版用Markdownを生成する
"""

import os
import json
import logging
from datetime import date, datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# ロギング設定
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 著者情報（固定値）
# ---------------------------------------------------------------------------
AUTHOR_NAME = "ミリテク"
AUTHOR_TAGLINE = "FXで言語化を学び、再現性を全ジャンルに翻訳する"
NOTE_MAGAZINE_URL = "https://note.com/militech_2077/m/mf82e085b93c9"
ZENN_URL = "https://zenn.dev/militech_2077"

# ---------------------------------------------------------------------------
# プロジェクトルート
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = PROJECT_ROOT / "articles"
DRAFTS_DIR = PROJECT_ROOT / "businesses" / "note_言語化全1論" / "data" / "drafts"
OUTPUT_DIR = PROJECT_ROOT / "kindle" / "output"


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def get_last_month(today: date | None = None) -> tuple[int, int]:
    """先月の (year, month) を返す"""
    today = today or date.today()
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Zenn形式の frontmatter (---で囲まれたYAML) を解析する。
    戻り値: (frontmatter_dict, body_text)
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}

    body = parts[2].lstrip("\n")
    return fm, body


def collect_zenn_articles(year: int, month: int) -> list[dict]:
    """
    articles/ から先月作成した published: true の Markdown を収集する。
    ファイル名の先頭8桁 (YYYYMMDD) で月フィルタリングする。
    """
    articles = []
    prefix = f"{year}{month:02d}"

    if not ARTICLES_DIR.exists():
        logger.warning("articles/ ディレクトリが見つかりません: %s", ARTICLES_DIR)
        return articles

    for md_file in sorted(ARTICLES_DIR.glob("*.md")):
        name = md_file.stem
        # ファイル名が YYYYMMDD で始まるものを対象にする
        if not name[:8].isdigit():
            continue
        if not name.startswith(prefix):
            continue

        text = md_file.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)

        if not fm.get("published", False):
            logger.debug("スキップ (published: false): %s", md_file.name)
            continue

        title = fm.get("title", md_file.stem)
        articles.append({"title": title, "body": body, "file": md_file.name})
        logger.info("Zenn記事を収集: %s", md_file.name)

    return articles


def collect_note_drafts(year: int, month: int) -> list[dict]:
    """
    businesses/note_言語化全1論/data/drafts/ 配下の JSON を収集する。
    ファイルの mtime で月フィルタリングする。
    """
    drafts = []

    if not DRAFTS_DIR.exists():
        logger.warning("drafts/ ディレクトリが見つかりません: %s", DRAFTS_DIR)
        return drafts

    for json_file in sorted(DRAFTS_DIR.rglob("*.json")):
        mtime = datetime.fromtimestamp(json_file.stat().st_mtime)
        if mtime.year != year or mtime.month != month:
            continue

        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("JSON解析エラー %s: %s", json_file.name, exc)
            continue

        title = data.get("title", json_file.stem)
        body = data.get("body", data.get("content", ""))
        if not body:
            logger.debug("本文なし、スキップ: %s", json_file.name)
            continue

        drafts.append({"title": title, "body": body, "file": json_file.name})
        logger.info("note下書きを収集: %s", json_file.name)

    return drafts


# ---------------------------------------------------------------------------
# 本文生成
# ---------------------------------------------------------------------------

def build_author_profile() -> str:
    return f"""# 著者について

**{AUTHOR_NAME}**

{AUTHOR_TAGLINE}

FXトレードの世界で「言語化」の重要性に気づき、その手法をビジネス・副業・節税・マインドセットなど全ジャンルへと翻訳・展開中。再現性のある思考フレームワークを発信し続けています。

- note マガジン: {NOTE_MAGAZINE_URL}
- Zenn: {ZENN_URL}

---
"""


def build_chapter(index: int, article: dict) -> str:
    title = article["title"]
    body = article["body"].strip()
    return f"## 第{index}章: {title}\n\n{body}\n\n---\n"


def build_footer() -> str:
    return f"""# さらに深く学ぶために

本書を最後まで読んでいただきありがとうございます。

より実践的なコンテンツは以下で継続発信しています。

## noteマガジン「言語化全1論」

{NOTE_MAGAZINE_URL}

月次でまとめた記事・テンプレート・実践ワークが読み放題のマガジンです。

## Zenn

{ZENN_URL}

エンジニア・副業オーナー向けの技術寄り記事を発信しています。

---

*{AUTHOR_NAME} — {AUTHOR_TAGLINE}*
"""


def build_book(title: str, articles: list[dict]) -> str:
    sections = [
        f"# {title}\n\n**著者: {AUTHOR_NAME}**\n\n---\n",
        build_author_profile(),
    ]

    for i, article in enumerate(articles, start=1):
        sections.append(build_chapter(i, article))

    sections.append(build_footer())
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def run(today: date | None = None) -> None:
    today = today or date.today()
    year, month = get_last_month(today)
    period_str = f"{year}-{month:02d}"

    logger.info("=== Kindle バンドラー開始 (対象月: %s) ===", period_str)

    # 記事収集
    zenn_articles = collect_zenn_articles(year, month)
    note_drafts = collect_note_drafts(year, month)
    all_articles = zenn_articles + note_drafts

    if not all_articles:
        logger.info("対象記事が0本のためスキップします (対象月: %s)", period_str)
        return

    # 本を組み立てる
    book_title = f"言語化全1論 {period_str} 月次まとめ"
    book_md = build_book(book_title, all_articles)

    # 出力
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"{period_str}_言語化全1論.md"
    output_file.write_text(book_md, encoding="utf-8")

    char_count = len(book_md)
    article_count = len(all_articles)
    logger.info("=== 生成完了 ===")
    logger.info("タイトル  : %s", book_title)
    logger.info("出力先    : %s", output_file)
    logger.info("記事数    : %d本 (Zenn: %d / note: %d)", article_count, len(zenn_articles), len(note_drafts))
    logger.info("文字数    : %s文字", f"{char_count:,}")


if __name__ == "__main__":
    run()
