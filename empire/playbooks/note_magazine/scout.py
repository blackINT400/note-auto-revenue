"""
Scout: トレンドトピックを収集し、Claude でニッチに合う上位5件を選定する
"""
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import anthropic
import feedparser

logger = logging.getLogger(__name__)


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
) -> list[dict]:
    """Claude を使ってニッチに合うトップ5トピックを選定する"""
    if candidates:
        candidate_text = "\n".join(
            f"- [{c['source']}] {c['title']}" for c in candidates[:40]
        )
        prompt = (
            f"あなたはnote.comの有料マガジン編集者です。\n"
            f"ニッチ: {niche}\n\n"
            f"以下のトレンド候補から、このニッチに最も適した上位5つのトピックを選んでください。\n\n"
            f"候補:\n{candidate_text}\n\n"
            f"各トピックについて以下のJSON形式で返してください（配列）:\n"
            f'[{{"title": "記事タイトル案", "keywords": ["kw1", "kw2", "kw3"], "reason": "選んだ理由（50字以内）"}}]\n\n'
            f"JSONのみ返してください。コードブロックや説明は不要です。"
        )
    else:
        prompt = (
            f"あなたはnote.comの有料マガジン編集者です。\n"
            f"ニッチ: {niche}\n\n"
            f"このニッチで今週バズりそうな記事トピックを5つ考えてください。\n\n"
            f"各トピックについて以下のJSON形式で返してください（配列）:\n"
            f'[{{"title": "記事タイトル案", "keywords": ["kw1", "kw2", "kw3"], "reason": "理由（50字以内）"}}]\n\n'
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


def run_scout(config: dict, data_dir: Path) -> list[dict]:
    """トレンドトピックを収集し上位5件を返す"""
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")
    tags = config.get("note_tags", ["副業", "節税", "フリーランス", "投資", "節約"])
    hatena_cats = config.get("hatena_categories", ["life", "it", "economics"])

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    # RSS 収集
    note_entries = _fetch_note_rss(tags)
    hatena_entries = _fetch_hatena_rss(hatena_cats)
    all_candidates = note_entries + hatena_entries
    logger.info("Total RSS candidates: %d", len(all_candidates))

    # Claude でトピック選定
    try:
        topics = _pick_topics_with_claude(client, model, niche, all_candidates)
    except Exception as exc:
        logger.error("Claude topic selection failed: %s", exc)
        # フォールバック: Claude にニッチのみ渡して生成
        try:
            topics = _pick_topics_with_claude(client, model, niche, [])
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
            {"candidates": all_candidates, "selected": topics},
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info("Saved topics to %s", out_path)
    return topics
