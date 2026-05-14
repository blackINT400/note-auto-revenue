"""
Scout: pytrends + はてなブックマーク RSS でキーワードを収集し
       Claude でSEO観点から上位3件を選定する
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


def _fetch_pytrends_keywords(niche: str, seed_keywords: list[str]) -> list[str]:
    """pytrends で関連キーワードを取得する（失敗時は空リスト）"""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="ja-JP", tz=540, timeout=(10, 25))
        seeds = seed_keywords[:5] if seed_keywords else [niche]
        pytrends.build_payload(seeds[:1], cat=0, timeframe="today 3-m", geo="JP")
        related = pytrends.related_queries()
        keywords = []
        for kw_data in related.values():
            top = kw_data.get("top")
            if top is not None and not top.empty:
                keywords.extend(top["query"].tolist()[:10])
        logger.info("pytrends keywords: %d", len(keywords))
        return keywords
    except Exception as exc:
        logger.warning("pytrends failed: %s", exc)
        return []


def _fetch_hatena_rss(categories: list[str]) -> list[str]:
    """はてなブックマーク RSS からタイトルを取得する"""
    titles = []
    for cat in categories:
        url = f"https://b.hatena.ne.jp/hotentry/{cat}.rss"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                if title:
                    titles.append(title)
            logger.info("Hatena RSS %s: %d entries", cat, len(feed.entries))
        except Exception as exc:
            logger.warning("Hatena RSS failed for %s: %s", cat, exc)
    return titles


def _load_history(data_dir: Path) -> dict:
    hist_path = data_dir / "data" / "keywords" / "history.json"
    try:
        return json.loads(hist_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_history(data_dir: Path, history: dict) -> None:
    hist_path = data_dir / "data" / "keywords" / "history.json"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def _select_keywords_with_claude(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    affiliate_links: list[dict],
    trend_signals: list[str],
    used_keywords: list[str],
) -> list[dict]:
    """Claude にSEO観点でキーワードを選定させる"""
    affiliate_desc = ", ".join(
        f"{a['name']}({a.get('description', '')})" for a in affiliate_links[:5]
    ) or "アフィリエイト商品なし"

    signals_text = "\n".join(f"- {s}" for s in trend_signals[:30]) or "（シグナルなし）"
    used_text = ", ".join(used_keywords[-20:]) or "なし"

    prompt = (
        f"あなたはSEO専門家です。以下の情報をもとに、アフィリエイトSEO記事に最適なキーワードを5つ選んでください。\n\n"
        f"ニッチ: {niche}\n"
        f"アフィリエイト商品: {affiliate_desc}\n\n"
        f"トレンドシグナル（Google Trends・はてなブックマーク）:\n{signals_text}\n\n"
        f"既に使用済みキーワード（重複禁止）: {used_text}\n\n"
        f"選定基準:\n"
        f"- 検索ボリュームがある程度あり、競合が少ない（ロングテール優先）\n"
        f"- 検索意図が明確（情報収集 or 購買意欲あり）\n"
        f"- アフィリエイト商品と関連性が高い\n\n"
        f"以下のJSON配列のみ返してください（コードブロック不要）:\n"
        f'[{{"keyword": "キーワード", "search_intent": "informational|transactional", '
        f'"affiliate_potential": "high|medium|low", "estimated_difficulty": "low|medium|high", '
        f'"reason": "選定理由（50字以内）"}}]'
    )

    response = client.messages.create(
        model=model,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    logger.info("Claude usage — input: %d, output: %d tokens",
                response.usage.input_tokens, response.usage.output_tokens)

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def run_scout(config: dict, data_dir: Path) -> list[dict]:
    """キーワードリサーチを実行し上位3件を返す"""
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")
    seed_keywords = config.get("seed_keywords", [niche])
    hatena_cats = config.get("hatena_categories", ["life", "it", "economics"])
    affiliate_links = config.get("affiliate_links", [])

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # トレンドシグナル収集
    pytrend_kws = _fetch_pytrends_keywords(niche, seed_keywords)
    hatena_titles = _fetch_hatena_rss(hatena_cats)
    all_signals = pytrend_kws + hatena_titles
    logger.info("Total trend signals: %d", len(all_signals))

    # 使用済みキーワードを除外
    history = _load_history(data_dir)
    used_keywords = list(history.keys())

    # Claude でキーワード選定
    try:
        candidates = _select_keywords_with_claude(
            client, model, niche, affiliate_links, all_signals, used_keywords
        )
    except Exception as exc:
        logger.error("Claude keyword selection failed: %s", exc)
        candidates = [
            {
                "keyword": f"{niche} おすすめ",
                "search_intent": "informational",
                "affiliate_potential": "medium",
                "estimated_difficulty": "low",
                "reason": "フォールバック",
            }
        ]

    # 高ポテンシャル順にソート (high > medium > low)
    priority = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda x: priority.get(x.get("affiliate_potential", "low"), 2))
    top3 = candidates[:3]

    # 履歴に追記
    today = date.today().isoformat()
    for kw_dict in top3:
        kw = kw_dict.get("keyword", "")
        if kw and kw not in history:
            history[kw] = {"added": today, **kw_dict}
    _save_history(data_dir, history)

    logger.info("Selected keywords: %s", [k.get("keyword") for k in top3])
    return top3
