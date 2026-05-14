"""
Scout: Product Hunt / はてなブックマーク からトレンドを収集し、
       Claude でデジタル商品アイデア3件を選定する
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

PRODUCT_TYPES = ["テンプレート集", "ノウハウPDF", "プロンプト集", "チェックリスト"]


def _fetch_product_hunt_rss() -> list[dict]:
    """Product Hunt RSS から上位20件を取得する"""
    url = "https://www.producthunt.com/feed?category=all"
    entries = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:20]:
            entries.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", "")[:200],
                "source": "product_hunt",
            })
        logger.info("Product Hunt RSS: %d entries", len(entries))
    except Exception as exc:
        logger.warning("Product Hunt RSS fetch failed: %s", exc)
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


def _load_existing_concepts(data_dir: Path) -> list[str]:
    """既存の products.jsonl から商品コンセプトを読み込む"""
    products_file = data_dir / "data" / "products.jsonl"
    concepts = []
    if products_file.exists():
        with open(products_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        concepts.append(obj.get("name", ""))
                    except json.JSONDecodeError:
                        pass
    return concepts


def _pick_opportunities_with_claude(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    candidates: list[dict],
    existing_concepts: list[str],
) -> list[dict]:
    """Claude を使ってデジタル商品アイデア3件を選定する"""
    candidate_text = "\n".join(
        f"- [{c['source']}] {c['title']}" for c in candidates[:40]
    ) if candidates else "（トレンド情報なし）"

    existing_text = "\n".join(f"- {c}" for c in existing_concepts) if existing_concepts else "（なし）"

    prompt = (
        f"あなたはデジタル商品プロデューサーです。\n"
        f"ニッチ: {niche}\n\n"
        f"以下のトレンド情報をもとに、Gumroadで販売できるデジタル商品のアイデアを3つ提案してください。\n\n"
        f"商品タイプ（いずれか）: {', '.join(PRODUCT_TYPES)}\n\n"
        f"トレンド情報:\n{candidate_text}\n\n"
        f"既に存在する商品（重複不可）:\n{existing_text}\n\n"
        f"各アイデアを以下のJSON形式で返してください（配列）:\n"
        f'[{{\n'
        f'  "title": "商品タイトル（日本語）",\n'
        f'  "type": "テンプレート集|ノウハウPDF|プロンプト集|チェックリスト",\n'
        f'  "target_audience": "ターゲット読者（20字以内）",\n'
        f'  "price_jpy": 980,\n'
        f'  "reason": "選んだ理由（50字以内）",\n'
        f'  "keywords": ["kw1", "kw2", "kw3"]\n'
        f'}}]\n\n'
        f"price_jpy は 500〜3000 の整数。JSONのみ返してください。"
    )

    response = client.messages.create(
        model=model,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    try:
        logger.info(
            "Claude usage — input: %d, output: %d tokens",
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
    except Exception:
        pass

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    opportunities = json.loads(raw)
    return opportunities[:3]


def run_scout(config: dict, data_dir: Path) -> list[dict]:
    """トレンドを収集しデジタル商品アイデア3件を返す"""
    niche = config.get("niche", "副業・AI活用・生産性向上")
    model = config.get("model", "claude-sonnet-4-6")
    hatena_cats = config.get("hatena_categories", ["it", "economics"])

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    ph_entries = _fetch_product_hunt_rss()
    hatena_entries = _fetch_hatena_rss(hatena_cats)
    all_candidates = ph_entries + hatena_entries
    logger.info("Total RSS candidates: %d", len(all_candidates))

    existing_concepts = _load_existing_concepts(data_dir)
    logger.info("Existing products: %d", len(existing_concepts))

    try:
        opportunities = _pick_opportunities_with_claude(
            client, model, niche, all_candidates, existing_concepts
        )
    except Exception as exc:
        logger.error("Claude opportunity selection failed: %s", exc)
        try:
            opportunities = _pick_opportunities_with_claude(
                client, model, niche, [], existing_concepts
            )
        except Exception as exc2:
            logger.error("Fallback opportunity generation failed: %s", exc2)
            opportunities = [
                {
                    "title": f"{niche}完全ガイド",
                    "type": "ノウハウPDF",
                    "target_audience": "初心者",
                    "price_jpy": 980,
                    "reason": "フォールバック",
                    "keywords": [niche],
                }
            ]

    out_path = data_dir / "data" / f"opportunities_{date.today()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"candidates": all_candidates, "selected": opportunities},
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info("Saved opportunities to %s", out_path)
    return opportunities
