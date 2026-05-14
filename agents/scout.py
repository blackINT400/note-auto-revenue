"""
scout.py: トレンド収集エージェント
はてブRSS + Google Trendsからニッチ関連キーワードを収集してJSONに保存する
"""
import json
import logging
from datetime import date
from pathlib import Path

import feedparser
import yaml

logger = logging.getLogger(__name__)

HATENA_RSS_URL = "https://b.hatena.ne.jp/hotentry/all.rss"
DATA_DIR = Path("data")


def _load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


def _fetch_hatena(niche_keywords: list) -> list:
    """はてブホットエントリからニッチ関連トレンドを取得"""
    try:
        feed = feedparser.parse(HATENA_RSS_URL)
        results = []
        for entry in feed.entries[:50]:
            title = entry.get("title", "")
            score = sum(1 for kw in niche_keywords if kw in title)
            results.append({
                "title": title,
                "link": entry.get("link", ""),
                "score": score,
                "tags": [t.term for t in getattr(entry, "tags", [])],
            })
        return sorted(results, key=lambda x: x["score"], reverse=True)[:20]
    except Exception as e:
        logger.warning(f"はてブ取得失敗: {e}")
        return []


def _fetch_google_trends(keywords: list) -> list:
    """Google Trendsから関連キーワードを取得"""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="ja-JP", tz=540, timeout=(10, 25))
        kws = [k for k in keywords if k][:3]
        if not kws:
            return []
        pytrends.build_payload(kws, geo="JP", timeframe="now 7-d")
        related = pytrends.related_queries()
        trending = []
        for data in related.values():
            if data and data.get("top") is not None:
                trending.extend(data["top"]["query"].tolist()[:5])
        return list(dict.fromkeys(trending))[:20]
    except Exception as e:
        logger.warning(f"Googleトレンド取得失敗（フォールバックで継続）: {e}")
        return []


def run() -> dict:
    config = _load_config()
    niche = config["niche"]
    niche_keywords = [k for k in niche.replace("・", " ").split() if k]

    logger.info(f"トレンド収集開始: {niche}")

    hatena = _fetch_hatena(niche_keywords)
    google = _fetch_google_trends(niche_keywords)
    strategy_keywords = config.get("auto_strategy", {}).get("top_keywords", [])

    result = {
        "date": str(date.today()),
        "niche": niche,
        "hatena": hatena,
        "google_trends": google,
        "strategy_keywords": strategy_keywords,
    }

    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"trends_{date.today()}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"トレンドデータ保存: {out_path} (はてブ{len(hatena)}件 / Googleトレンド{len(google)}件)")

    return result
