"""
empire_main.py: 帝国全体のオーケストレーター

--mode daily  : 全事業の日次処理 + CEO判断
--mode weekly : Scout（新市場スキャン）+ Launcher（新事業準備）+ CEO週次レポート
--mode monthly: 月次総括 + 戦略立案（毎月1日に自動実行）

GitHub Actionsスケジュール:
  毎日   07:00 JST: --mode daily
  毎週月曜 09:00 JST: --mode weekly
  毎月1日 10:00 JST: --mode monthly
"""
import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests
import yaml

from empire.utils import (
    PROJECT_ROOT, EMPIRE_DIR,
    get_empire_cost_limit, get_empire_month_cost,
    load_portfolio, save_portfolio,
)

# ── ログ設定 ──────────────────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"empire_{date.today()}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_WAIT = 30


# ── Slack通知 ─────────────────────────────────────────────────────────────────

def _slack(message: str):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    try:
        requests.post(url, json={"text": message}, timeout=10)
    except Exception as e:
        logger.warning(f"Slack通知失敗: {e}")


# ── リトライ付き実行 ──────────────────────────────────────────────────────────

def _run(func, name: str):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func()
        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"[{name}] エラー (試行 {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT)
            else:
                _slack(f"⚠️ *[{name}]* {MAX_RETRIES}回失敗しました。\nエラー: {e}")
                raise


# ── 安全装置: 帝国コスト上限 ─────────────────────────────────────────────────

def _empire_cost_guard() -> bool:
    """帝国エージェント（CEO/Scout/Launcher）のコストが上限の20%を超えたら停止"""
    empire_cost = get_empire_month_cost()
    limit = get_empire_cost_limit()
    threshold = limit * 0.20  # 帝国エージェント自体のコスト上限は全体の20%
    if empire_cost >= threshold:
        _slack(f"🛑 *[帝国]* エージェントのAPIコスト({empire_cost:.0f}円)が上限({threshold:.0f}円)に達しました。")
        return True
    return False


# ── 全事業の日次処理 ──────────────────────────────────────────────────────────

def _run_all_businesses_daily(portfolio: dict):
    """各事業の日次スクリプト（main.py --mode daily）をサブプロセスで実行する"""
    businesses = portfolio.get("businesses", [])
    for b in businesses:
        bid = b["id"]
        status = b.get("status", "")
        if status not in ("active",):
            logger.info(f"[帝国] {bid}: スキップ（status={status}）")
            continue

        data_dir = b.get("data_dir", "")
        if data_dir == ".":
            # 既存のZennシステム（プロジェクトルート）
            script_path = PROJECT_ROOT / "main.py"
            cwd = PROJECT_ROOT
        else:
            script_path = PROJECT_ROOT / data_dir / "main.py"
            cwd = PROJECT_ROOT / data_dir

        if not script_path.exists():
            logger.warning(f"[帝国] {bid}: main.py が見つかりません ({script_path})")
            continue

        logger.info(f"[帝国] {bid} 日次処理を開始します...")
        try:
            result = subprocess.run(
                [sys.executable, str(script_path), "--mode", "daily"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=600,  # 10分タイムアウト
                env={**os.environ},
            )
            if result.returncode == 0:
                logger.info(f"[帝国] {bid}: 完了")
            else:
                logger.error(f"[帝国] {bid}: エラー終了 (code={result.returncode})\n{result.stderr[:500]}")
        except subprocess.TimeoutExpired:
            logger.error(f"[帝国] {bid}: タイムアウト（10分）")
        except Exception as e:
            logger.error(f"[帝国] {bid}: 実行エラー: {e}")


# ── 月次総括 ──────────────────────────────────────────────────────────────────

def run_monthly_summary(portfolio: dict):
    """月次総括: 全事業のサマリーを生成して Slack に送信する"""
    today = date.today()
    if today.month == 1:
        target_year, target_month = today.year - 1, 12
    else:
        target_year, target_month = today.year, today.month - 1

    target_prefix = f"{target_year}-{target_month:02d}"
    businesses = portfolio.get("businesses", [])
    empire_kpi = portfolio.get("empire_kpi", {})

    # 全事業サマリー
    biz_lines = []
    for b in businesses:
        biz_lines.append(
            f"| {b['id']} | {b.get('status', '')} | "
            f"{b.get('monthly_revenue', 0):,.0f}円 | "
            f"{b.get('monthly_cost', 0):,.0f}円 | "
            f"{b.get('roi', 0):.1f}% |"
        )
    biz_table = "\n".join(biz_lines) or "| なし | - | - | - | - |"

    report = f"""# 帝国月次総括 {target_year}年{target_month}月

生成日: {today}

## 帝国KPI

- **月間総収益**: {empire_kpi.get('total_monthly_revenue', 0):,.0f}円
- **月間総コスト**: {empire_kpi.get('total_monthly_cost', 0):,.0f}円
- **稼働事業数**: {empire_kpi.get('business_count', 0)}件
- **トップ事業**: {empire_kpi.get('best_performer', 'なし')}

## 事業別実績

| 事業ID | ステータス | 収益 | コスト | ROI |
|--------|-----------|------|--------|-----|
{biz_table}

---
*自動生成 by empire_main.py*
"""

    output_path = LOG_DIR / f"empire_monthly_{target_prefix}.md"
    output_path.write_text(report, encoding="utf-8")
    logger.info(f"[帝国] 月次サマリー保存: {output_path}")

    # Slack通知
    slack_msg = (
        f"📅 *{target_year}年{target_month}月 帝国月次レポート*\n"
        f"月間総収益: {empire_kpi.get('total_monthly_revenue', 0):,.0f}円\n"
        f"月間総コスト: {empire_kpi.get('total_monthly_cost', 0):,.0f}円\n"
        f"稼働事業数: {empire_kpi.get('business_count', 0)}件\n"
        f"詳細: `{output_path.name}`"
    )
    _slack(slack_msg)


# ── モード別処理 ──────────────────────────────────────────────────────────────

def run_daily():
    logger.info("=" * 60)
    logger.info("帝国 日次処理 開始")
    logger.info("=" * 60)

    portfolio = load_portfolio()

    # 安全装置: コスト上限チェック（全事業合計）
    cost_limit = get_empire_cost_limit()
    total_biz_cost = sum(float(b.get("monthly_cost", 0)) for b in portfolio.get("businesses", []))
    if total_biz_cost >= cost_limit:
        _slack(f"🛑 *[帝国]* 全事業の月間コスト合計({total_biz_cost:,.0f}円)が上限({cost_limit:,.0f}円)に達しました。日次処理を停止します。")
        logger.error("帝国コスト上限超過。日次処理を中断します。")
        return

    # 全アクティブ事業を実行
    _run_all_businesses_daily(portfolio)

    # CEO判断
    if not _empire_cost_guard():
        from empire import ceo_agent
        _run(lambda: ceo_agent.run(slack_fn=_slack, weekly_report=False), "CEO（日次判断）")

    _slack(f"✅ *[帝国] 日次処理完了* ({date.today()})")
    logger.info("帝国 日次処理 完了")


def run_weekly():
    logger.info("=" * 60)
    logger.info("帝国 週次処理 開始")
    logger.info("=" * 60)

    if _empire_cost_guard():
        logger.warning("帝国エージェントのコスト上限により週次処理をスキップします")
        return

    portfolio = load_portfolio()

    # Scout: 新市場スキャン
    from empire import scout_agent
    opportunities = _run(scout_agent.run, "Scout（新市場スキャン）")

    # Launcher: 新事業準備（候補があれば）
    if opportunities:
        from empire import launcher_agent
        new_bid = _run(lambda: launcher_agent.run(opportunities, slack_fn=_slack), "Launcher（新事業準備）")
        if new_bid:
            logger.info(f"[帝国] 新事業「{new_bid}」の準備完了。Slackで承認を確認してください。")

    # CEO: 週次レポート付き判断
    from empire import ceo_agent
    _run(lambda: ceo_agent.run(slack_fn=_slack, weekly_report=True), "CEO（週次判断 + レポート）")

    logger.info("帝国 週次処理 完了")


def run_monthly():
    logger.info("=" * 60)
    logger.info("帝国 月次総括 開始")
    logger.info("=" * 60)

    portfolio = load_portfolio()
    _run(lambda: run_monthly_summary(portfolio), "月次総括")

    logger.info("帝国 月次総括 完了")


# ── エントリーポイント ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="帝国オーケストレーター")
    parser.add_argument(
        "--mode",
        choices=["daily", "weekly", "monthly"],
        required=True,
        help="daily: 日次処理 / weekly: スカウト+ポートフォリオ最適化 / monthly: 月次総括",
    )
    args = parser.parse_args()

    try:
        if args.mode == "daily":
            run_daily()
        elif args.mode == "weekly":
            run_weekly()
        else:
            run_monthly()
    except SystemExit as e:
        if str(e) == "COST_LIMIT_EXCEEDED":
            _slack("🛑 *[帝国]* APIコスト上限に達しました。今月の処理を停止します。")
            sys.exit(0)
        raise


if __name__ == "__main__":
    main()
