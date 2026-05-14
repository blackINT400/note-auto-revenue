"""
dashboard/collector.py: 全事業のKPIデータを収集・集計して月次JSONに保存する

呼び出し元:
  - empire_main.py --mode daily の最後（自動）
  - 手動: python dashboard/collector.py

出力先: dashboard/data/kpi_YYYY-MM.json
"""
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from empire.utils import (
    load_portfolio,
    get_empire_cost_limit,
    get_empire_month_cost,
)

logger = logging.getLogger(__name__)

DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
DATA_DIR = DASHBOARD_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"


# ── ユーティリティ ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    return {}


def _kpi_path(year_month: str) -> Path:
    return DATA_DIR / f"kpi_{year_month}.json"


def _load_kpi(year_month: str) -> dict:
    path = _kpi_path(year_month)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "year_month": year_month,
        "snapshots": [],       # 日次スナップショット
        "history": [],         # 過去12ヶ月月次サマリー
    }


def _save_kpi(data: dict, year_month: str):
    path = _kpi_path(year_month)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[collector] KPI保存: {path.name}")


# ── 過去12ヶ月履歴の収集 ─────────────────────────────────────────────────────

def _build_history() -> list[dict]:
    """過去12ヶ月分の月次サマリーを kpi_YYYY-MM.json から集める"""
    today = date.today()
    history = []

    for i in range(12):
        # 今月から i ヶ月前
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        ym = f"{year}-{month:02d}"
        path = _kpi_path(ym)
        if not path.exists():
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        snapshots = data.get("snapshots", [])
        if not snapshots:
            continue

        # その月の最新スナップショットを月次代表値として使う
        latest = snapshots[-1]
        total = latest.get("total", {})
        history.insert(0, {
            "year_month": ym,
            "revenue": total.get("revenue", 0),
            "cost": total.get("cost", 0),
            "profit": total.get("profit", 0),
            "roi": total.get("roi", 0),
            "business_count": len(latest.get("businesses", [])),
        })

    return history


# ── メイン収集処理 ─────────────────────────────────────────────────────────────

def collect() -> dict:
    """KPIを収集してJSONに保存し、スナップショットデータを返す"""
    today = date.today()
    year_month = today.strftime("%Y-%m")
    config = _load_config()

    # ── portfolio から各事業データを取得 ──────────────────────────────────────
    portfolio = load_portfolio()
    businesses_raw = portfolio.get("businesses", [])
    empire_kpi = portfolio.get("empire_kpi", {})
    revenue_pool = portfolio.get("revenue_pool", {})

    # 各事業のKPIスナップショット
    biz_list = []
    total_revenue = 0.0
    total_cost = 0.0

    for b in businesses_raw:
        rev = float(b.get("monthly_revenue", 0))
        cost = float(b.get("monthly_cost", 0))
        profit = rev - cost
        roi = (profit / cost * 100) if cost > 0 else 0.0

        total_revenue += rev
        total_cost += cost

        biz_list.append({
            "id": b.get("id", ""),
            "name": b.get("id", ""),        # 表示名はIDで代替
            "type": b.get("type", ""),
            "revenue": round(rev, 0),
            "cost": round(cost, 0),
            "profit": round(profit, 0),
            "roi": round(roi, 1),
            "status": b.get("status", "active"),
        })

    # 帝国エージェントのAPIコストも総コストに加算
    empire_agent_cost = get_empire_month_cost()
    total_cost += empire_agent_cost

    total_profit = total_revenue - total_cost
    total_roi = (total_profit / total_cost * 100) if total_cost > 0 else 0.0

    # 月間コスト上限と使用率
    cost_limit = get_empire_cost_limit()
    cost_usage_pct = round((total_cost / cost_limit * 100), 1) if cost_limit > 0 else 0.0

    # 目標収益（config.yaml から、なければ portfolio の目標）
    monthly_target = float(config.get("monthly_revenue_target", 30000))

    # ── スナップショット構築 ──────────────────────────────────────────────────
    snapshot = {
        "date": str(today),
        "total": {
            "revenue": round(total_revenue, 0),
            "cost": round(total_cost, 0),
            "profit": round(total_profit, 0),
            "roi": round(total_roi, 1),
            "cost_limit": cost_limit,
            "cost_usage_pct": cost_usage_pct,
            "monthly_target": monthly_target,
            "available_budget": float(revenue_pool.get("available_budget", 0)),
        },
        "businesses": biz_list,
    }

    # ── 既存JSONに追記（同日付があれば上書き） ───────────────────────────────
    kpi_data = _load_kpi(year_month)

    # 同日スナップショットを更新 or 追加
    existing = kpi_data.get("snapshots", [])
    replaced = False
    for i, snap in enumerate(existing):
        if snap.get("date") == str(today):
            existing[i] = snapshot
            replaced = True
            break
    if not replaced:
        existing.append(snapshot)
    kpi_data["snapshots"] = existing

    # 過去12ヶ月履歴を更新
    kpi_data["history"] = _build_history()

    _save_kpi(kpi_data, year_month)
    logger.info(
        f"[collector] {today} スナップショット完了 "
        f"収益:{total_revenue:,.0f}円 コスト:{total_cost:,.0f}円 純利益:{total_profit:,.0f}円"
    )
    return snapshot


# ── エントリーポイント ────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    collect()
    print("collector: 完了")
