"""
Pattern Analyzer: noteの人気記事を収集・分析してパターンをowner/note_patterns.jsonに保存する

STEP1: note.comスクレイピング（ジャンル×4、各20本）
STEP2: Claude APIでタイトル型・本文構造・刺さる理由を抽出
STEP3: owner/note_patterns.jsonに保存（週次蓄積）
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic
import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
PATTERNS_PATH = PROJECT_ROOT / "owner" / "note_patterns.json"
CONTEXT_PROMPT_PATH = PROJECT_ROOT / "owner" / "context_prompt.md"

GENRES = [
    ("恋愛", ["恋愛", "好きな人", "片思い", "別れ", "パートナー"]),
    ("自己成長", ["自己成長", "自己肯定感", "習慣", "メンタル", "自分を変える"]),
    ("人間関係", ["人間関係", "職場", "友人", "家族", "コミュニケーション"]),
    ("哲学", ["哲学", "言語化", "思考", "生き方", "本質"]),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://note.com/",
}


# ── STEP1: スクレイピング ──────────────────────────────────────────────────────

def _fetch_note_search_api(keyword: str, size: int = 20) -> list[dict]:
    """note検索API（v3）でスキ数順の記事を取得する"""
    articles = []
    try:
        url = f"https://note.com/api/v3/searches?q={keyword}&context=note&page=1&sort=popular&size={size}"
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            notes = (
                data.get("data", {}).get("notes", {}).get("contents", [])
                or data.get("data", {}).get("contents", [])
                or []
            )
            for n in notes:
                title = n.get("name", "") or n.get("title", "")
                likes = int(n.get("likeCount", 0) or n.get("like_count", 0))
                user = (n.get("user", {}) or {}).get("urlname", "")
                key = n.get("key", "")
                hashtags = [t.get("name", "") for t in (n.get("hashtag_notes", []) or [])]
                if title:
                    articles.append({
                        "title": title,
                        "like_count": likes,
                        "url": f"https://note.com/{user}/n/{key}" if user and key else "",
                        "hashtags": hashtags,
                        "genre": keyword,
                    })
            logger.info("note API [%s]: %d件", keyword, len(articles))
    except Exception as exc:
        logger.debug("note API失敗 [%s]: %s", keyword, exc)
    return articles


def _fetch_note_html_search(keyword: str, size: int = 20) -> list[dict]:
    """note.com検索ページのHTML (__NEXT_DATA__)から記事を取得するフォールバック"""
    articles = []
    try:
        from bs4 import BeautifulSoup
        url = f"https://note.com/search?q={keyword}&context=note&order=like"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return articles
        soup = BeautifulSoup(resp.text, "lxml")
        next_data = soup.find("script", id="__NEXT_DATA__")
        if next_data and next_data.string:
            data = json.loads(next_data.string)
            notes = (
                data.get("props", {})
                    .get("pageProps", {})
                    .get("initialState", {})
                    .get("noteSearchResult", {})
                    .get("contents", [])
                or []
            )
            for n in notes[:size]:
                title = n.get("name", "") or n.get("title", "")
                likes = int(n.get("likeCount", 0))
                if title:
                    articles.append({
                        "title": title,
                        "like_count": likes,
                        "url": "",
                        "hashtags": [],
                        "genre": keyword,
                    })
            logger.info("note HTML [%s]: %d件", keyword, len(articles))
        else:
            # __NEXT_DATA__がない場合: OGタグ等からタイトルを拾う
            for card in soup.select("a.o-timelineNotesItem__link, a[data-gtm-click-label]")[:size]:
                title_el = card.select_one("h3, .o-timelineNotesItem__title")
                if title_el:
                    title = title_el.get_text(strip=True)
                    if title:
                        articles.append({
                            "title": title,
                            "like_count": 0,
                            "url": "",
                            "hashtags": [],
                            "genre": keyword,
                        })
    except ImportError:
        logger.warning("beautifulsoup4/lxml が未インストール — HTMLスクレイピングをスキップ")
    except Exception as exc:
        logger.debug("note HTML失敗 [%s]: %s", keyword, exc)
    return articles


def collect_popular_articles(genres: list[tuple] | None = None) -> list[dict]:
    """全ジャンルのnote人気記事を収集する（最大20本×ジャンル）"""
    if genres is None:
        genres = GENRES

    all_articles: list[dict] = []
    seen_titles: set[str] = set()

    for genre_name, keywords in genres:
        genre_articles: list[dict] = []
        for kw in keywords[:2]:  # 各ジャンル上位2キーワードで検索
            articles = _fetch_note_search_api(kw, size=20)
            if not articles:
                time.sleep(1)
                articles = _fetch_note_html_search(kw, size=20)
            for a in articles:
                if a["title"] not in seen_titles:
                    a["genre_label"] = genre_name
                    genre_articles.append(a)
                    seen_titles.add(a["title"])
            time.sleep(0.5)

        # スキ数降順でトップ20
        genre_articles.sort(key=lambda x: x.get("like_count", 0), reverse=True)
        all_articles.extend(genre_articles[:20])
        logger.info("ジャンル [%s]: %d本収集", genre_name, len(genre_articles[:20]))

    logger.info("合計: %d本収集", len(all_articles))
    return all_articles


# ── STEP2: Claude APIでパターン分析 ───────────────────────────────────────────

def _analyze_patterns_with_claude(
    client: anthropic.Anthropic,
    model: str,
    articles: list[dict],
    context_prompt: str,
) -> dict:
    """収集した記事からタイトル型・本文構造・刺さる理由を抽出する"""

    # ジャンル別にグループ化してリスト化
    by_genre: dict[str, list[str]] = {}
    for a in articles:
        g = a.get("genre_label", "その他")
        by_genre.setdefault(g, []).append(
            f"・{a['title']}" + (f"（スキ:{a['like_count']}）" if a.get("like_count") else "")
        )

    genre_text = "\n\n".join(
        f"【{g}】\n" + "\n".join(lines[:20])
        for g, lines in by_genre.items()
    )

    prompt = f"""あなたはnote.comのコンテンツ戦略家です。
以下はnoteで実際に人気を集めている記事のタイトルリストです。

{genre_text}

以下のオーナーの思考OS（context_prompt）も参照してください:
---
{context_prompt[:1500]}
---

このデータから以下をJSONで出力してください:

{{
  "title_patterns": {{
    "avg_length_chars": "平均文字数（数値）",
    "effective_structures": ["効果的なタイトル構造を3つ（例: 断言型・問い型・共感型）"],
    "power_words": ["クリックされやすいワードを5つ"],
    "avoid_words": ["使わない方がいいワードを3つ"],
    "best_examples": ["上位3タイトルをそのまま引用"],
    "title_formula": "タイトル生成の公式を一文で（例: 読者の感情状態+断言+具体性）"
  }},
  "body_patterns": {{
    "opening_types": ["冒頭の書き出しパターンを3つ（例: 感情共感・事実断言・問いかけ）"],
    "optimal_heading_count": "最適見出し数（数値）",
    "heading_style": "見出しの文体傾向",
    "conclusion_style": "結論の置き方"
  }},
  "resonance_structures": [
    {{
      "genre": "ジャンル名",
      "surface_trend": "表面的なトレンド",
      "abstract_structure": "なぜ刺さるかの本質（構造）",
      "reader_psychology": "読者の深層心理",
      "replicable_pattern": "オーナーの思想で再現するパターン"
    }}
  ],
  "market_insight": "今のnoteで何が求められているかの一言サマリー（50字以内）"
}}

JSONのみ出力してください。コードブロック不要。"""

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
            logger.info(
                "パターン分析完了 — タイトル公式: %s",
                result.get("title_patterns", {}).get("title_formula", "")[:60],
            )
            return result
    except Exception as exc:
        logger.error("パターン分析失敗: %s", exc)
    return {}


# ── STEP3: note_patterns.json への保存・蓄積 ──────────────────────────────────

def load_patterns() -> dict:
    """owner/note_patterns.jsonを読み込む（なければ空dict）"""
    if not PATTERNS_PATH.exists():
        return {"analysis_history": [], "latest": {}}
    try:
        return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"analysis_history": [], "latest": {}}


def save_patterns(patterns: dict) -> None:
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATTERNS_PATH.write_text(
        json.dumps(patterns, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("パターン保存: %s", PATTERNS_PATH)


def _diff_summary(prev: dict, current: dict) -> str:
    """前週との差分を簡潔にまとめる"""
    prev_kw = set(prev.get("title_patterns", {}).get("power_words", []))
    curr_kw = set(current.get("title_patterns", {}).get("power_words", []))
    added = curr_kw - prev_kw
    removed = prev_kw - curr_kw
    lines = []
    if added:
        lines.append(f"新出パワーワード: {', '.join(added)}")
    if removed:
        lines.append(f"消えたパワーワード: {', '.join(removed)}")
    prev_insight = prev.get("market_insight", "")
    curr_insight = current.get("market_insight", "")
    if prev_insight != curr_insight:
        lines.append(f"市場変化: 「{prev_insight}」→「{curr_insight}」")
    return " / ".join(lines) if lines else "大きな変化なし"


# ── メインエントリーポイント ──────────────────────────────────────────────────

def run_pattern_analyzer(config: dict, data_dir: Path) -> dict:
    """週次: note人気記事を収集・分析してパターンをowner/note_patterns.jsonに保存する"""
    logger.info("=== Pattern Analyzer 開始 ===")

    model = config.get("model", "claude-sonnet-4-5-20251022")
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    context_prompt = ""
    if CONTEXT_PROMPT_PATH.exists():
        context_prompt = CONTEXT_PROMPT_PATH.read_text(encoding="utf-8")

    # STEP1: 収集
    articles = collect_popular_articles()

    # 収集結果をキャッシュ保存
    raw_path = data_dir / "data" / f"note_popular_{date.today()}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not articles:
        logger.warning("記事が0件 — 分析をスキップ")
        return {}

    # STEP2: 分析
    analysis = _analyze_patterns_with_claude(client, model, articles, context_prompt)
    if not analysis:
        return {}

    # STEP3: 蓄積保存
    patterns = load_patterns()
    prev_latest = patterns.get("latest", {})
    diff = _diff_summary(prev_latest, analysis)

    entry = {
        "date": str(date.today()),
        "article_count": len(articles),
        "diff_from_prev": diff,
        "analysis": analysis,
    }
    patterns.setdefault("analysis_history", []).append(entry)
    # 直近4週分だけ保持
    patterns["analysis_history"] = patterns["analysis_history"][-4:]
    patterns["latest"] = analysis
    patterns["last_updated"] = str(date.today())

    save_patterns(patterns)

    logger.info("=== Pattern Analyzer 完了: diff=%s ===", diff)
    return analysis
