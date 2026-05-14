"""
Scout: RapidAPI marketplace調査 + Claude でAPI機会を特定する
"""
import json
import logging
import os
from datetime import date
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

_FALLBACK_CATEGORIES = [
    "Text Analysis", "Translation", "Data Validation", "SEO Tools",
    "Image Processing", "E-commerce", "Finance", "Entertainment",
]

_SEED_IDEAS = [
    "日本語テキスト分析API", "SEOキーワード提案API", "商品説明文生成API",
    "画像Alt文生成API", "レビュー感情分析API", "メール件名最適化API",
    "コード説明API", "FAQ自動生成API",
]


def _fetch_rapidapi_categories() -> list[str]:
    """RapidAPI マーケットプレイスからトレンドカテゴリを取得する。失敗時はフォールバック。"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://rapidapi.com/marketplace",
            headers={"User-Agent": "Mozilla/5.0 (compatible; ScoutBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # カテゴリっぽい文字列を粗く抽出 (class="category" や heading タグ周辺)
        import re
        cats = re.findall(r'(?:category|Category)["\s:>]+([A-Za-z &]+)', html)
        cats = [c.strip() for c in cats if 3 < len(c.strip()) < 40]
        unique = list(dict.fromkeys(cats))[:12]
        if unique:
            logger.info("RapidAPI categories fetched: %s", unique)
            return unique
    except Exception as exc:
        logger.warning("RapidAPI fetch failed: %s — using fallback", exc)
    return _FALLBACK_CATEGORIES


def _fetch_producthunt_tools() -> list[str]:
    """Product Hunt RSS からAPI/ツール系プロダクト名を取得する。"""
    try:
        import urllib.request
        import xml.etree.ElementTree as ET
        req = urllib.request.Request(
            "https://www.producthunt.com/feed?category=all",
            headers={"User-Agent": "Mozilla/5.0 (compatible; ScoutBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        titles = []
        for item in root.iter("item"):
            title_el = item.find("title")
            desc_el = item.find("description")
            title = title_el.text if title_el is not None else ""
            desc = desc_el.text if desc_el is not None else ""
            combined = f"{title} {desc}".lower()
            if any(kw in combined for kw in ("api", "tool", "automation", "ai", "saas")):
                titles.append(title.strip())
        logger.info("Product Hunt API tools: %d found", len(titles))
        return titles[:10]
    except Exception as exc:
        logger.warning("Product Hunt fetch failed: %s", exc)
        return []


def _load_existing_services(data_dir: Path) -> list[str]:
    services_file = data_dir / "data" / "services.jsonl"
    names = []
    if services_file.exists():
        for line in services_file.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if "name" in obj:
                        names.append(obj["name"].lower())
                except json.JSONDecodeError:
                    pass
    return names


def run_scout(config: dict, data_dir: Path) -> list[dict]:
    """RapidAPI + Product Hunt を調査し、Claudeで3件のAPI機会を特定して返す。"""
    categories = _fetch_rapidapi_categories()
    ph_tools = _fetch_producthunt_tools()
    existing = _load_existing_services(data_dir)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = config.get("model", "claude-sonnet-4-6")

    prompt = f"""あなたはAPIビジネスのコンサルタントです。
以下の情報をもとに、RapidAPIで収益化できる新しいAPIサービスのアイデアを3件提案してください。

【RapidAPIトレンドカテゴリ】
{', '.join(categories)}

【Product Huntの注目ツール】
{', '.join(ph_tools) if ph_tools else 'データなし'}

【既存サービス（重複禁止）】
{', '.join(existing) if existing else 'なし'}

【参考アイデア例】
{', '.join(_SEED_IDEAS)}

【条件】
- Python/FastAPI + Claude API をバックエンドに使って実装できるもの
- RapidAPIでの明確なマネタイズポテンシャルがあるもの
- 市場が飽和していないニッチなもの
- 日本語市場または英語市場どちらでも可

以下のJSON配列形式で厳密に返してください（JSONのみ、説明文なし）:
[
  {{
    "name": "サービス名（英語スネークケース）",
    "description": "サービスの説明（日本語、100字以内）",
    "endpoint_examples": ["POST /analyze", "GET /health"],
    "pricing_tier": "Basic: 無料100回/月, Pro: $9.99/月 無制限",
    "target_customers": "ターゲット顧客",
    "reason": "選定理由（50字以内）"
  }}
]"""

    logger.info("Calling Claude to identify API opportunities...")
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # JSON 部分だけ抽出
    import re
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        logger.error("Claude did not return valid JSON array")
        return []
    opportunities: list[dict] = json.loads(match.group())

    # 既存サービスと重複するものを除外
    filtered = [o for o in opportunities if o.get("name", "").lower() not in existing]

    # 保存
    out_path = data_dir / "data" / f"opportunities_{date.today()}.json"
    out_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2))
    logger.info("Saved %d opportunities to %s", len(filtered), out_path)

    return filtered
