"""
Analyzer: Gumroad API から販売データを取得し、Claude でインサイトを生成する
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests

logger = logging.getLogger(__name__)

GUMROAD_API_BASE = "https://api.gumroad.com/v2"


def _fetch_gumroad_sales(token: str) -> list[dict]:
    """Gumroad API から販売データを取得する"""
    sales = []
    try:
        resp = requests.get(
            f"{GUMROAD_API_BASE}/sales",
            params={"access_token": token},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            sales = data.get("sales", [])
            logger.info("Fetched %d sales from Gumroad", len(sales))
    except Exception as exc:
        logger.warning("Failed to fetch Gumroad sales: %s", exc)
    return sales


def _fetch_gumroad_products(token: str) -> list[dict]:
    """Gumroad API からプロダクト一覧を取得する"""
    products = []
    try:
        resp = requests.get(
            f"{GUMROAD_API_BASE}/products",
            params={"access_token": token},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            products = data.get("products", [])
            logger.info("Fetched %d products from Gumroad", len(products))
    except Exception as exc:
        logger.warning("Failed to fetch Gumroad products: %s", exc)
    return products


def _calc_metrics_from_sales(sales: list[dict]) -> dict:
    """販売データからメトリクスを計算する"""
    now = datetime.now(timezone.utc)
    ym = f"{now.year}-{now.month:02d}"

    total_revenue_cents = 0
    monthly_count = 0
    product_counts: dict[str, int] = {}

    for sale in sales:
        price_cents = sale.get("price", 0) or 0
        # GumroadはUSD、1USD≈150JPYで換算
        total_revenue_cents += price_cents
        created = sale.get("created_at", "")
        if created.startswith(ym):
            monthly_count += 1
        pname = sale.get("product_name", "unknown")
        product_counts[pname] = product_counts.get(pname, 0) + 1

    revenue_jpy = int(total_revenue_cents / 100 * 150)
    top_products = sorted(product_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "sales_count": len(sales),
        "monthly_sales_count": monthly_count,
        "revenue_jpy": revenue_jpy,
        "top_products": [{"name": n, "sales": c} for n, c in top_products],
    }


def _calc_metrics_from_local(data_dir: Path) -> dict:
    """products.jsonl からローカル推定メトリクスを計算する"""
    jsonl_path = data_dir / "data" / "products.jsonl"
    products = []
    if jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        products.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    listed = [p for p in products if p.get("status") == "listed"]
    revenue_est = sum(p.get("price_jpy", 0) for p in listed)

    return {
        "sales_count": len(listed),
        "monthly_sales_count": len(listed),
        "revenue_jpy": revenue_est,
        "top_products": [
            {"name": p.get("name", ""), "sales": 1} for p in listed[:5]
        ],
        "data_source": "local_estimate",
    }


def _analyze_with_claude(
    client: anthropic.Anthropic,
    model: str,
    metrics: dict,
    products: list[dict],
) -> dict:
    """Claude を使って販売分析とレコメンデーションを生成する"""
    products_summary = json.dumps(
        [{"name": p.get("name", ""), "sales_count": p.get("sales_count", 0)} for p in products[:10]],
        ensure_ascii=False,
    )

    prompt = (
        f"あなたはデジタル商品ビジネスのアナリストです。\n"
        f"以下の販売データを分析し、次のアクションを提案してください。\n\n"
        f"販売メトリクス:\n{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        f"商品一覧（上位）:\n{products_summary}\n\n"
        f"以下のJSON形式で返してください:\n"
        f'{{\n'
        f'  "analysis": "現状分析（100字以内）",\n'
        f'  "top_performing_type": "最も売れている商品タイプ",\n'
        f'  "next_product_suggestion": "次に作るべき商品のアイデア（50字以内）",\n'
        f'  "pricing_recommendation": "価格調整のアドバイス（50字以内）",\n'
        f'  "recommendations": ["推奨アクション1", "推奨アクション2", "推奨アクション3"]\n'
        f'}}\n\n'
        f"JSONのみ返してください。"
    )

    response = client.messages.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def run_analyzer(config: dict, data_dir: Path) -> dict:
    """販売データを分析してインサイトを返す"""
    model = config.get("model", "claude-sonnet-4-6")
    scale_threshold = config.get("scale_trigger_sales_count", 10)
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)
    token = os.environ.get("GUMROAD_API_TOKEN", "")

    if token:
        sales = _fetch_gumroad_sales(token)
        gumroad_products = _fetch_gumroad_products(token)
        metrics = _calc_metrics_from_sales(sales)
        product_count = len(gumroad_products)
    else:
        logger.info("GUMROAD_API_TOKEN not set; using local data for analysis")
        metrics = _calc_metrics_from_local(data_dir)
        gumroad_products = []
        product_count = metrics["sales_count"]

    scale_trigger = metrics.get("monthly_sales_count", 0) >= scale_threshold

    try:
        insights = _analyze_with_claude(client, model, metrics, gumroad_products)
    except Exception as exc:
        logger.error("Claude analysis failed: %s", exc)
        insights = {
            "analysis": "分析データ不足",
            "top_performing_type": "不明",
            "next_product_suggestion": "ノウハウPDFを追加",
            "pricing_recommendation": "現状維持",
            "recommendations": ["商品数を増やす", "SNSで告知する", "価格を見直す"],
        }

    result = {
        "sales_count": metrics.get("sales_count", 0),
        "revenue_jpy": metrics.get("revenue_jpy", 0),
        "product_count": product_count,
        "top_products": metrics.get("top_products", []),
        "recommendations": insights.get("recommendations", []),
        "analysis": insights.get("analysis", ""),
        "next_product_suggestion": insights.get("next_product_suggestion", ""),
        "pricing_recommendation": insights.get("pricing_recommendation", ""),
        "scale_trigger": scale_trigger,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    now = datetime.now(timezone.utc)
    out_path = data_dir / "data" / f"analysis_{now.year}-{now.month:02d}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("Saved analysis to %s", out_path)

    return result
