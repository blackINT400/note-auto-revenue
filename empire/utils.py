"""
empire/utils.py: 帝国エージェント共通ユーティリティ
全エージェントがここからポートフォリオ操作・コスト記録・モデル取得・Discord通知を行う
"""
import json
import logging
import os
from datetime import date
from pathlib import Path

import requests
import yaml

EMPIRE_DIR = Path(__file__).parent
PROJECT_ROOT = EMPIRE_DIR.parent

# ── .env 自動読み込み（ローカル開発用）────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_path = PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass
PORTFOLIO_PATH = EMPIRE_DIR / "portfolio.yaml"
EMPIRE_COST_PATH = EMPIRE_DIR / "data" / "empire_cost.json"

# ── Discord通知 ────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

logger = logging.getLogger(__name__)


def notify(title: str, body: str, urgent: bool = False) -> None:
    """Discord embed で通知する。urgent=True で赤色表示。"""
    if not DISCORD_WEBHOOK_URL:
        logger.debug("DISCORD_WEBHOOK_URL 未設定 — 通知をスキップします")
        return
    color = 0xFF4444 if urgent else 0x1D9E75
    # Discord embed の description は 4096 文字上限
    desc = body[:4000] + "\n…（省略）" if len(body) > 4000 else body
    payload = {
        "embeds": [{
            "title": title[:256],
            "description": desc,
            "color": color,
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.warning("Discord通知失敗: %d %s", resp.status_code, resp.text[:100])
    except Exception as exc:
        logger.warning("Discord通知エラー: %s", exc)

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


def load_portfolio() -> dict:
    return yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def save_portfolio(portfolio: dict):
    PORTFOLIO_PATH.write_text(
        yaml.dump(portfolio, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def record_empire_cost(agent: str, input_tokens: int, output_tokens: int) -> float:
    """帝国エージェント（CEO/Scout/Launcher）のAPIコストを記録して累計を返す（円）"""
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    jpy = usd * JPY_RATE
    current_month = str(date.today())[:7]

    EMPIRE_COST_PATH.parent.mkdir(exist_ok=True)
    if EMPIRE_COST_PATH.exists():
        try:
            tracker = json.loads(EMPIRE_COST_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            tracker = {}
        if tracker.get("month") != current_month:
            tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}
    else:
        tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}

    tracker["total_jpy"] = round(tracker["total_jpy"] + jpy, 2)
    tracker["calls"].append({
        "date": str(date.today()),
        "agent": agent,
        "cost_jpy": round(jpy, 2),
    })
    EMPIRE_COST_PATH.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")
    return tracker["total_jpy"]


def get_empire_month_cost() -> float:
    if not EMPIRE_COST_PATH.exists():
        return 0.0
    try:
        tracker = json.loads(EMPIRE_COST_PATH.read_text(encoding="utf-8"))
        if tracker.get("month") != str(date.today())[:7]:
            return 0.0
        return tracker.get("total_jpy", 0.0)
    except (json.JSONDecodeError, OSError):
        return 0.0


def get_model() -> str:
    """プロジェクトルートの config.yaml からモデル名を取得"""
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            return cfg.get("model", "claude-sonnet-4-6")
        except Exception:
            pass
    return "claude-sonnet-4-6"


def get_empire_cost_limit() -> float:
    """portfolio.yaml から月間コスト上限を取得"""
    try:
        portfolio = load_portfolio()
        return float(portfolio.get("revenue_pool", {}).get("monthly_cost_limit", 5000))
    except Exception:
        return 5000.0
