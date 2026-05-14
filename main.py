"""
main.py: 全エージェントを統括するエントリーポイント
--mode daily  : トレンド収集 → 記事生成 → Zenn投稿
--mode weekly : 収益分析 → config.yaml自動更新
"""
import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests
import yaml

# ── ログ設定 ──────────────────────────────────────────────────────────────────
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"{date.today()}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_WAIT_SEC = 30

# ── Slack通知 ─────────────────────────────────────────────────────────────────

def _slack(message: str):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        logger.debug("SLACK_WEBHOOK_URLが未設定のため通知をスキップ")
        return
    try:
        requests.post(url, json={"text": message}, timeout=10)
    except Exception as e:
        logger.warning(f"Slack通知失敗: {e}")


# ── リトライ付き実行 ──────────────────────────────────────────────────────────

def _run(func, name: str):
    """エラー時は最大3回リトライ。3回失敗でSlack通知 + 例外を再送出"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func()
            logger.info(f"[{name}] 完了")
            return result
        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"[{name}] エラー (試行 {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"{RETRY_WAIT_SEC}秒後にリトライします...")
                time.sleep(RETRY_WAIT_SEC)
            else:
                msg = f"⚠️ *[{name}]* {MAX_RETRIES}回連続でエラーが発生しました。システムを停止します。\nエラー: {e}"
                logger.critical(msg)
                _slack(msg)
                raise


# ── 日次処理 ──────────────────────────────────────────────────────────────────

def run_daily():
    logger.info("=" * 60)
    logger.info("日次処理 開始")
    logger.info("=" * 60)

    from agents import scout, writer, publisher

    _run(scout.run, "Scout（トレンド収集）")

    articles = _run(writer.run, "Writer（記事生成）")

    if not articles:
        msg = "⚠️ 今日は記事を生成できませんでした（コスト上限または品質基準未達）"
        logger.warning(msg)
        _slack(msg)
        return

    results = _run(lambda: publisher.run(articles), "Publisher（Zenn投稿）")

    title_list = "\n".join(f"• {r.get('title', '')}" for r in results)
    _slack(f"✅ *本日の記事投稿完了* ({len(results)}件)\n{title_list}")
    logger.info(f"日次処理 完了: {len(results)}件の記事を投稿")

    # 毎月1日に月次サマリーを生成
    if date.today().day == 1:
        logger.info("月初のため月次サマリーを生成します")
        from agents import analyst
        path = _run(analyst.generate_monthly_summary, "MonthlyReport（月次サマリー）")
        if path:
            _slack(f"📅 *月次サマリーを生成しました*\n`{path}`")


# ── 週次処理 ──────────────────────────────────────────────────────────────────

def run_weekly():
    logger.info("=" * 60)
    logger.info("週次分析 開始")
    logger.info("=" * 60)

    from agents import analyst

    _run(analyst.run, "Analyst（戦略分析）")

    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    keywords = config.get("auto_strategy", {}).get("top_keywords", [])
    report = config.get("auto_strategy", {}).get("weekly_report", "")
    kw_str = ", ".join(keywords[:5]) if keywords else "なし"
    _slack(f"📊 *週次分析完了*\n戦略キーワード: {kw_str}\n{report}")
    logger.info("週次分析 完了")


# ── エントリーポイント ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Zenn自動投稿システム")
    parser.add_argument("--mode", choices=["daily", "weekly"], required=True,
                        help="daily: 日次処理 / weekly: 週次分析")
    args = parser.parse_args()

    try:
        if args.mode == "daily":
            run_daily()
        else:
            run_weekly()
    except SystemExit as e:
        if str(e) == "COST_LIMIT_EXCEEDED":
            _slack("🛑 *月間APIコスト上限に達しました*\n今月の自動実行を停止します。来月1日に自動再開します。")
            sys.exit(0)
        raise


if __name__ == "__main__":
    main()
