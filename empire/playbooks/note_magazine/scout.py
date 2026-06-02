"""
Scout: トレンドトピックを収集し、Claude でニッチに合う上位5件を選定する
STEP1: noteトレンド人気記事トップ20取得
STEP2: Claude APIで「なぜ読まれているか」を抽象化
"""
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import anthropic
import feedparser
import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
PATTERNS_PATH = PROJECT_ROOT / "owner" / "note_patterns.json"


def _load_patterns() -> dict:
    """owner/note_patterns.jsonを読み込む"""
    if not PATTERNS_PATH.exists():
        return {}
    try:
        return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fetch_note_trending(tags: list[str]) -> list[dict]:
    """note.com API + RSS で人気記事トップ20を取得する"""
    entries = []
    seen_titles: set[str] = set()

    for tag in tags[:4]:
        # note API（like順）を試みる
        fetched_from_api = False
        try:
            api_url = (
                f"https://note.com/api/v2/notes"
                f"?context=hashtag&hashtag={tag}&size=20&sort=like"
            )
            resp = requests.get(api_url, timeout=10, headers={"Accept": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                notes = (
                    data.get("data", {}).get("notes", [])
                    or data.get("notes", [])
                )
                for note in notes:
                    title = note.get("name", "") or note.get("title", "")
                    likes = int(note.get("like_count", 0))
                    if title and title not in seen_titles:
                        entries.append({
                            "title": title,
                            "like_count": likes,
                            "source": f"note/api/{tag}",
                        })
                        seen_titles.add(title)
                if notes:
                    fetched_from_api = True
                    logger.info("note API %s: %d entries", tag, len(notes))
        except Exception as exc:
            logger.debug("note API fetch failed for %s: %s", tag, exc)

        if fetched_from_api:
            continue

        # フォールバック: RSS
        try:
            feed = feedparser.parse(f"https://note.com/hashtag/{tag}?format=rss")
            for entry in feed.entries[:20]:
                title = entry.get("title", "")
                if title and title not in seen_titles:
                    entries.append({
                        "title": title,
                        "like_count": 0,
                        "source": f"note/rss/{tag}",
                    })
                    seen_titles.add(title)
            logger.info("note RSS %s: %d entries", tag, len(feed.entries))
        except Exception as exc:
            logger.warning("note RSS fetch failed for %s: %s", tag, exc)

    # like_count 降順でソート → 上位20件
    entries.sort(key=lambda x: x.get("like_count", 0), reverse=True)
    return entries[:20]


def _abstractize_with_claude(
    client: anthropic.Anthropic,
    model: str,
    trending_articles: list[dict],
) -> dict:
    """STEP2: 人気記事リストから『なぜ読まれているか』を構造的に分析する"""
    if not trending_articles:
        return {}

    articles_text = "\n".join(
        f"- {a['title']}"
        + (f"（スキ: {a['like_count']}）" if a.get("like_count") else "")
        for a in trending_articles[:20]
    )

    prompt = f"""以下の人気記事リストを見て
「なぜこの記事が読まれているのか」を
表面的なテーマではなく構造的に分析してください

例:
表面: すね毛の記事が人気
抽象: 他者評価への不安を言語化してくれる記事が刺さる
構造: 読者が「感じているが言語化できていなかった」ことを代わりに言語化する記事は必ず読まれる

{articles_text}

以下をJSON形式で出力:
{{
  "surface_trend": "表面的なトレンド",
  "abstract_structure": "なぜ読まれているかの本質",
  "reader_psychology": "読者の深層心理",
  "replicable_pattern": "この構造を別テーマで再現するパターン",
  "reference_title": "参考にした人気記事のタイトル（最も代表的な1本）"
}}

JSONのみ出力してください。"""

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
            logger.info("抽象化完了: %s", result.get("abstract_structure", "")[:60])
            return result
    except Exception as exc:
        logger.warning("抽象化処理失敗: %s", exc)

    return {}


def _fetch_note_rss(tags: list[str]) -> list[dict]:
    """note.com ハッシュタグ RSS からエントリを取得する"""
    entries = []
    for tag in tags:
        url = f"https://note.com/hashtag/{tag}?format=rss"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                entries.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                    "source": f"note/{tag}",
                })
            logger.info("note RSS %s: %d entries", tag, len(feed.entries))
        except Exception as exc:
            logger.warning("note RSS fetch failed for %s: %s", tag, exc)
    return entries


def _fetch_hatena_rss(categories: list[str]) -> list[dict]:
    """はてなブックマーク RSS からエントリを取得する"""
    entries = []
    for cat in categories:
        url = f"https://b.hatena.ne.jp/hotentry/{cat}.rss"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                entries.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                    "source": f"hatena/{cat}",
                })
            logger.info("Hatena RSS %s: %d entries", cat, len(feed.entries))
        except Exception as exc:
            logger.warning("Hatena RSS fetch failed for %s: %s", cat, exc)
    return entries


def _pick_topics_with_claude(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    candidates: list[dict],
    patterns: dict | None = None,
) -> list[dict]:
    """Claude を使ってニッチに合うトップ5トピックを選定する（パターンデータ活用）"""

    # パターンデータからタイトル生成のヒントを組み立てる
    pattern_hint = ""
    if patterns and patterns.get("latest"):
        latest = patterns["latest"]
        tp = latest.get("title_patterns", {})
        formula = tp.get("title_formula", "")
        power_words = tp.get("power_words", [])
        avoid_words = tp.get("avoid_words", [])
        market_insight = latest.get("market_insight", "")
        if formula or power_words:
            pattern_hint = (
                f"\n\n## note人気記事のパターン分析（必ず活用）\n"
                f"タイトル公式: {formula}\n"
                f"パワーワード: {', '.join(power_words[:5])}\n"
                f"避けるワード: {', '.join(avoid_words[:3])}\n"
                f"今の市場: {market_insight}\n"
                f"タイトルは20文字以内・具体的な感情や場面を入れること。"
            )

    if candidates:
        candidate_text = "\n".join(
            f"- [{c['source']}] {c['title']}" for c in candidates[:40]
        )
        prompt = (
            f"あなたはnote.comの有料マガジン編集者です。\n"
            f"ニッチ: {niche}\n\n"
            f"以下のトレンド候補から、このニッチに最も適した上位5つのトピックを選んでください。\n\n"
            f"候補:\n{candidate_text}"
            f"{pattern_hint}\n\n"
            f"各トピックについて以下のJSON形式で返してください（配列）:\n"
            f'[{{"title": "20文字以内の記事タイトル", "keywords": ["kw1", "kw2", "kw3"], "reason": "選んだ理由（50字以内）"}}]\n\n'
            f"JSONのみ返してください。コードブロックや説明は不要です。"
        )
    else:
        prompt = (
            f"あなたはnote.comの有料マガジン編集者です。\n"
            f"ニッチ: {niche}\n\n"
            f"このニッチで今週バズりそうな記事トピックを5つ考えてください。"
            f"{pattern_hint}\n\n"
            f"各トピックについて以下のJSON形式で返してください（配列）:\n"
            f'[{{"title": "20文字以内の記事タイトル", "keywords": ["kw1", "kw2", "kw3"], "reason": "理由（50字以内）"}}]\n\n'
            f"JSONのみ返してください。コードブロックや説明は不要です。"
        )

    response = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    try:
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        logger.info(
            "Claude usage — input: %d, output: %d tokens", input_tokens, output_tokens
        )
    except Exception:
        pass

    # JSON 部分を抽出
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    topics = json.loads(raw)
    return topics[:5]


def run_scout(config: dict, data_dir: Path) -> tuple[list[dict], dict]:
    """
    STEP1: noteトレンド人気記事トップ20取得
    STEP2: Claude で抽象化（なぜ読まれているか）
    STEP3: ニッチに合うトピック上位5件を選定

    Returns: (topics, abstraction_meta)
    """
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")
    tags = config.get("note_tags", ["副業", "節税", "フリーランス", "投資", "節約"])
    hatena_cats = config.get("hatena_categories", ["life", "it", "economics"])

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    # ── STEP1: note人気記事トップ20 ─────────────────────────────────────────
    trending = _fetch_note_trending(tags)
    logger.info("STEP1 完了: note人気記事 %d件取得", len(trending))

    # ── STEP2: Claude で抽象化 ───────────────────────────────────────────────
    abstraction_meta: dict = {}
    if trending:
        abstraction_meta = _abstractize_with_claude(client, model, trending)
        logger.info("STEP2 完了: 抽象構造=%s", abstraction_meta.get("abstract_structure", "")[:40])
    else:
        logger.warning("STEP2 スキップ: トレンド記事が取得できませんでした")

    # ── RSS 収集（既存の候補も補完に使う）───────────────────────────────────
    note_rss_entries = _fetch_note_rss(tags)
    hatena_entries = _fetch_hatena_rss(hatena_cats)
    # trending のタイトルも候補に含める
    trending_as_candidates = [
        {"title": a["title"], "summary": "", "source": a["source"]}
        for a in trending
    ]
    all_candidates = trending_as_candidates + note_rss_entries + hatena_entries
    logger.info("Total candidates: %d", len(all_candidates))

    # ── パターンデータ読み込み ────────────────────────────────────────────────
    patterns = _load_patterns()
    if patterns.get("latest"):
        logger.info("note_patterns.json 読み込み済み (last_updated: %s)", patterns.get("last_updated", ""))
    else:
        logger.info("note_patterns.json なし — パターンなしで実行")

    # ── STEP3: Claude でトピック選定 ─────────────────────────────────────────
    try:
        topics = _pick_topics_with_claude(client, model, niche, all_candidates, patterns)
    except Exception as exc:
        logger.error("Claude topic selection failed: %s", exc)
        try:
            topics = _pick_topics_with_claude(client, model, niche, [], patterns)
        except Exception as exc2:
            logger.error("Fallback topic generation also failed: %s", exc2)
            topics = [
                {
                    "title": f"{niche}の基本ガイド",
                    "keywords": [niche],
                    "reason": "フォールバック",
                }
            ]

    # キャッシュ保存
    out_path = data_dir / "data" / f"topics_{date.today()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "trending": trending,
                "abstraction": abstraction_meta,
                "candidates": all_candidates,
                "selected": topics,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info("Saved topics to %s", out_path)
    return topics, abstraction_meta
