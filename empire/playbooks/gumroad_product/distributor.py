"""
Distributor: Gumroad API を使ってデジタル商品を出品する
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

GUMROAD_API_BASE = "https://api.gumroad.com/v2"


def _jpy_to_cents(price_jpy: int) -> int:
    """円をGumroad用センタ（USD近似）に変換する（1USD≈150JPY）"""
    usd = price_jpy / 150.0
    cents = max(99, int(round(usd * 100)))  # minimum 99 cents
    return cents


def _post_to_gumroad(token: str, product: dict) -> dict:
    """Gumroad API でプロダクトを作成する"""
    price_cents = _jpy_to_cents(product.get("price_jpy", 980))
    payload = {
        "access_token": token,
        "name": product.get("product_title", product.get("title", "Digital Product")),
        "description": product.get("description", ""),
        "price": price_cents,
        "currency": "usd",
    }
    resp = requests.post(
        f"{GUMROAD_API_BASE}/products",
        data=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Gumroad API error: {data.get('message', 'unknown')}")
    return data.get("product", {})


def _save_to_products_jsonl(data_dir: Path, record: dict) -> None:
    """products.jsonl に1行追加する"""
    jsonl_path = data_dir / "data" / "products.jsonl"
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _save_pending(data_dir: Path, product: dict) -> str:
    """API トークンなしの場合、pending ディレクトリに保存する"""
    pending_dir = data_dir / "data" / "products" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    slug = product.get("slug", f"product-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    pending_path = pending_dir / f"{slug}.json"
    with open(pending_path, "w", encoding="utf-8") as f:
        json.dump(product, f, ensure_ascii=False, indent=2)
    return str(pending_path)


def _count_monthly_sales(data_dir: Path) -> int:
    """products.jsonl から今月の販売数を集計する"""
    jsonl_path = data_dir / "data" / "products.jsonl"
    if not jsonl_path.exists():
        return 0
    today = datetime.now(timezone.utc)
    ym = f"{today.year}-{today.month:02d}"
    count = 0
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("created_at", "").startswith(ym):
                    count += 1
            except json.JSONDecodeError:
                pass
    return count


def run_distributor(config: dict, data_dir: Path, products: list) -> list[dict]:
    """商品リストをGumroadに出品する（トークン未設定時はpending保存）"""
    token = os.environ.get("GUMROAD_API_TOKEN", "")
    scale_threshold = config.get("scale_trigger_sales_count", 10)
    results = []

    monthly_count = _count_monthly_sales(data_dir)
    scale_triggered = monthly_count >= scale_threshold

    for product in products:
        name = product.get("product_title", product.get("title", "Digital Product"))
        price_jpy = product.get("price_jpy", 980)
        now_str = datetime.now(timezone.utc).isoformat()

        if not token:
            logger.warning(
                "GUMROAD_API_TOKEN not set. Saving '%s' as pending_upload.", name
            )
            logger.info(
                "手動アップロード手順: https://app.gumroad.com/products/new\n"
                "  - Name: %s\n  - Price: ¥%d\n  - Description: %s",
                name,
                price_jpy,
                product.get("description", "")[:100],
            )
            pending_path = _save_pending(data_dir, product)
            record = {
                "product_id": None,
                "name": name,
                "price_jpy": price_jpy,
                "gumroad_url": None,
                "status": "pending_upload",
                "pending_path": pending_path,
                "created_at": now_str,
                "scale_trigger": scale_triggered,
            }
            results.append(record)
            _save_to_products_jsonl(data_dir, record)
            continue

        try:
            gumroad_product = _post_to_gumroad(token, product)
            product_id = gumroad_product.get("id", "")
            gumroad_url = gumroad_product.get("short_url", "") or gumroad_product.get("url", "")
            record = {
                "product_id": product_id,
                "name": name,
                "price_jpy": price_jpy,
                "gumroad_url": gumroad_url,
                "status": "listed",
                "created_at": now_str,
                "scale_trigger": scale_triggered,
            }
            logger.info("Listed on Gumroad: %s (%s)", name, gumroad_url)
        except Exception as exc:
            logger.error("Gumroad API failed for '%s': %s", name, exc)
            pending_path = _save_pending(data_dir, product)
            record = {
                "product_id": None,
                "name": name,
                "price_jpy": price_jpy,
                "gumroad_url": None,
                "status": "pending_upload",
                "pending_path": pending_path,
                "error": str(exc),
                "created_at": now_str,
                "scale_trigger": scale_triggered,
            }

        results.append(record)
        _save_to_products_jsonl(data_dir, record)

    logger.info(
        "Distributor done: %d products, scale_trigger=%s", len(results), scale_triggered
    )
    return results
