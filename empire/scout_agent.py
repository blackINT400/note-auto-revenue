"""
scout_agent.py: 新市場を常時スキャンし機会を採点する週次エージェント

スコアリング（各10点満点・合計35点超で展開候補）:
  市場規模 / 競合の少なさ / 既存資産との相性 / 初期コストの低さ / 自動化可能度
"""
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import anthropic
import feedparser

from empire.utils import (
    PROJECT_ROOT, get_model,
    load_portfolio, record_empire_cost,
)

logger = logging.getLogger(__name__)

OPPORTUNITIES_LOG = PROJECT_ROOT / "empire" / "data" / "opportunities.jsonl"
SCORE_THRESHOLD = 35
MAX_ACTIVE_BUSINESSES = 5
MIN_AVAILABLE_BUDGET = 10_000


# ── 展開条件チェック ──────────────────────────────────────────────────────────

def _check_launch_conditions(portfolio: dict) -> tuple:
    """新事業展開の3条件をすべてチェックする（条件1・2のみ。条件3はスコアリング後）"""
    revenue_pool = portfolio.get("revenue_pool", {})
    businesses = portfolio.get("businesses", [])

    available_budget = float(revenue_pool.get("available_budget", 0))
    active_count = len([
        b for b in businesses
        if b.get("status") not in ("terminated", "pending_human_approval")
    ])

    if available_budget < MIN_AVAILABLE_BUDGET:
        return False, f"収益プール不足（{available_budget:,.0f}円 < {MIN_AVAILABLE_BUDGET:,.0f}円）"
    if active_count >= MAX_ACTIVE_BUSINESSES:
        return False, f"事業数上限（{active_count}件 >= {MAX_ACTIVE_BUSINESSES}件）"
    return True, f"条件クリア（残高:{available_budget:,.0f}円 / 事業数:{active_count}件）"


# ── データ収集 ────────────────────────────────────────────────────────────────

def _fetch_rising_keywords(niche_keywords: list) -> list:
    """Google Trendsから急上昇キーワードを取得"""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="ja-JP", tz=540, timeout=(10, 25))
        kws = [k for k in niche_keywords if k][:3]
        if not kws:
            return []
        pytrends.build_payload(kws, geo="JP", timeframe="now 7-d")
        rising_data = pytrends.related_queries()
        keywords = []
        for data in rising_data.values():
            if data and data.get("rising") is not None:
                keywords.extend(data["rising"]["query"].tolist()[:5])
        return list(dict.fromkeys(keywords))[:15]
    except Exception as e:
        logger.warning(f"[Scout] Googleトレンド取得失敗: {e}")
        return []


def _fetch_product_hunt_trends() -> list:
    """Product Hunt RSSからトレンドプロダクトを取得"""
    try:
        feed = feedparser.parse("https://www.producthunt.com/feed?category=all")
        items = []
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            summary = entry.get("summary", "")[:150]
            if title:
                items.append(f"{title}: {summary}")
        return items[:10]
    except Exception as e:
        logger.warning(f"[Scout] Product Hunt取得失敗: {e}")
        return []


def _extract_reader_demands() -> list:
    """data/learnings.json の theme_insights からニーズキーワードを取得"""
    learnings_path = PROJECT_ROOT / "data" / "learnings.json"
    if not learnings_path.exists():
        return []
    try:
        learnings = json.loads(learnings_path.read_text(encoding="utf-8"))
        keywords = []
        for insight in learnings.get("theme_insights", [])[-3:]:
            keywords.extend(insight.get("keywords", []))
        return list(dict.fromkeys(keywords))[:10]
    except (json.JSONDecodeError, OSError):
        return []


# ── スコアリング ──────────────────────────────────────────────────────────────

def _score_opportunity(opportunity: str, existing_niches: list,
                       client: anthropic.Anthropic, model: str) -> dict:
    """Claude APIで機会をスコアリング（5軸×10点満点）"""
    existing_str = "、".join(existing_niches) if existing_niches else "なし"

    prompt = f"""あなたは新規事業の評価専門家です。以下の市場機会を5つの軸で評価してください。

【評価対象】{opportunity}
【既存事業のジャンル】{existing_str}

各軸を0〜10の整数で採点し、以下のセクション区切りで厳密に出力してください:

---SCORE_START---
市場規模: スコア（0-10の整数のみ）
競合の少なさ: スコア（0-10の整数のみ）
既存資産との相性: スコア（0-10の整数のみ）
初期コストの低さ: スコア（0-10の整数のみ）
自動化可能度: スコア（0-10の整数のみ）
推薦理由: 50字以内
推薦プラットフォーム: Zenn/WordPress/Gumroad/API（最も適切な1つ）
推薦ニッチ: この機会を活かした具体的なニッチ（20字以内）
---SCORE_END---"""

    response = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.content[0].text
    record_empire_cost("scout", response.usage.input_tokens, response.usage.output_tokens)

    match = re.search(r"---SCORE_START---\s*(.*?)\s*---SCORE_END---", content, re.DOTALL)
    if not match:
        logger.warning(f"[Scout] スコアパース失敗: {opportunity[:30]}")
        return {"opportunity": opportunity, "total_score": 0}

    block = match.group(1).strip()

    def extract_int(key: str) -> int:
        m = re.search(rf"^{key}:\s*(\d+)", block, re.MULTILINE)
        return min(int(m.group(1)), 10) if m else 0

    def extract_str(key: str) -> str:
        m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
        return m.group(1).strip() if m else ""

    scores = {
        "市場規模": extract_int("市場規模"),
        "競合の少なさ": extract_int("競合の少なさ"),
        "既存資産との相性": extract_int("既存資産との相性"),
        "初期コストの低さ": extract_int("初期コストの低さ"),
        "自動化可能度": extract_int("自動化可能度"),
    }
    total = sum(scores.values())

    return {
        "date": str(date.today()),
        "opportunity": opportunity,
        "scores": scores,
        "total_score": total,
        "reason": extract_str("推薦理由"),
        "recommended_platform": extract_str("推薦プラットフォーム"),
        "recommended_niche": extract_str("推薦ニッチ"),
    }


def _log_opportunity(opportunity: dict):
    OPPORTUNITIES_LOG.parent.mkdir(exist_ok=True)
    with OPPORTUNITIES_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(opportunity, ensure_ascii=False) + "\n")


# ── メイン ────────────────────────────────────────────────────────────────────

def run() -> list:
    """週次スキャン: 3条件クリア時のみスコア35点超の展開候補リストを返す"""
    portfolio = load_portfolio()

    # 安全装置: 条件1（収益プール）& 条件2（事業数）チェック
    can_launch, reason = _check_launch_conditions(portfolio)
    if not can_launch:
        logger.info(f"[Scout] 新事業展開条件未達のためスキャンをスキップします: {reason}")
        return []
    logger.info(f"[Scout] 展開条件チェック: {reason}")

    businesses = portfolio.get("businesses", [])
    existing_niches = [b.get("niche", "") for b in businesses if b.get("niche")]

    import yaml
    config_path = PROJECT_ROOT / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    niche_keywords = config.get("niche", "副業").replace("・", " ").split()
    model = get_model()

    logger.info("[Scout] 市場スキャン開始...")

    # データ収集
    rising = _fetch_rising_keywords(niche_keywords)
    ph_items = _fetch_product_hunt_trends()
    reader_demands = _extract_reader_demands()

    # 機会リストを作成（重複除去）
    candidates = list(dict.fromkeys(rising[:10] + ph_items[:5] + reader_demands[:5]))
    if not candidates:
        logger.info("[Scout] スキャン結果なし")
        return []

    logger.info(f"[Scout] {len(candidates)}件をスコアリング中...")
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    top_opportunities = []
    for opp in candidates[:15]:  # コスト節約のため最大15件
        try:
            scored = _score_opportunity(opp, existing_niches, client, model)
            _log_opportunity(scored)
            if scored["total_score"] >= SCORE_THRESHOLD:
                top_opportunities.append(scored)
                logger.info(
                    f"[Scout] 展開候補発見: {opp[:30]} "
                    f"({scored['total_score']}点 / {scored.get('recommended_platform', '')})"
                )
        except Exception as e:
            logger.warning(f"[Scout] スコアリングエラー ({opp[:20]}): {e}")

    top_opportunities.sort(key=lambda x: x["total_score"], reverse=True)
    logger.info(f"[Scout] 展開候補: {len(top_opportunities)}件 (閾値: {SCORE_THRESHOLD}点)")
    return top_opportunities
