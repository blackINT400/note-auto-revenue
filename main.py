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

import yaml

from empire.utils import notify

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
                msg = f"{MAX_RETRIES}回連続でエラーが発生しました。システムを停止します。\nエラー: {e}"
                logger.critical(msg)
                notify(f"⚠️ [{name}] 連続エラー", msg, urgent=True)
                raise


# ── 日次処理 ──────────────────────────────────────────────────────────────────

def run_daily():
    logger.info("=" * 60)
    logger.info("日次処理 開始")
    logger.info("=" * 60)

    from empire.report_generator import ReportCollector
    with ReportCollector("daily") as rc:
        rc.add_action("Zenn自動投稿システム 日次処理 開始")

        from agents import scout, writer, publisher

        try:
            _run(scout.run, "Scout（トレンド収集）")
            rc.add_success("トレンドスキャン完了")
        except Exception as e:
            rc.add_failure("トレンドスキャン失敗", cause=str(e)[:100])

        try:
            articles = _run(writer.run, "Writer（記事生成）")
        except Exception as e:
            rc.add_failure("記事生成失敗", cause=str(e)[:100])
            articles = []

        if not articles:
            logger.warning("今日は記事を生成できませんでした（コスト上限または品質基準未達）")
            notify("⚠️ 記事生成なし", "コスト上限または品質基準未達のため生成をスキップしました。", urgent=True)
            rc.add_failure("記事生成なし", cause="コスト上限または品質基準未達")
        else:
            try:
                results = _run(lambda: publisher.run(articles), "Publisher（Zenn投稿）")
                title_list = "\n".join(f"・{r.get('title', '')}" for r in results)
                notify("✅ 本日の記事投稿完了", f"{len(results)}件\n{title_list}")
                logger.info(f"日次処理 完了: {len(results)}件の記事を投稿")
                for r in results:
                    rc.add_success(f"Zenn投稿完了: {r.get('title', '')[:40]}")

                # Threads投稿文生成・配信（ACCESS_TOKEN未設定時はDiscord通知のみ）
                try:
                    from agents import social
                    social.run(results)
                    rc.add_success(f"Threads投稿文生成完了: {len(results)}件")
                except Exception as e:
                    logger.warning("Threads投稿文生成失敗（続行）: %s", e)
                    rc.add_failure("Threads投稿文生成失敗", cause=str(e)[:100], needs_action=False)

            except Exception as e:
                rc.add_failure("Zenn投稿失敗", cause=str(e)[:100], needs_action=True)

        # 毎月1日に月次サマリーを生成
        if date.today().day == 1:
            logger.info("月初のため月次サマリーを生成します")
            from agents import analyst
            try:
                path = _run(analyst.generate_monthly_summary, "MonthlyReport（月次サマリー）")
                if path:
                    notify("📅 月次サマリー生成", f"`{path}`")
                    rc.add_success(f"月次サマリー生成: {path}")
            except Exception as e:
                rc.add_failure("月次サマリー生成失敗", cause=str(e)[:100])


# ── 週次処理 ──────────────────────────────────────────────────────────────────

def run_weekly():
    logger.info("=" * 60)
    logger.info("週次分析 開始")
    logger.info("=" * 60)

    from empire.report_generator import ReportCollector
    with ReportCollector("weekly") as rc:
        rc.add_action("Zenn自動投稿システム 週次分析 開始")

        from agents import analyst
        try:
            _run(analyst.run, "Analyst（戦略分析）")
            rc.add_success("週次戦略分析完了")
        except Exception as e:
            rc.add_failure("週次分析失敗", cause=str(e)[:100])

        config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        keywords = config.get("auto_strategy", {}).get("top_keywords", [])
        report = config.get("auto_strategy", {}).get("weekly_report", "")
        kw_str = ", ".join(keywords[:5]) if keywords else "なし"
        notify("📊 週次分析完了", f"戦略キーワード: {kw_str}\n{report}")
        logger.info("週次分析 完了")


# ── エントリーポイント ────────────────────────────────────────────────────────

def _validate_config() -> None:
    """起動時にconfig.yamlの制約違反を自動修正する"""
    config_path = Path("config.yaml")
    if not config_path.exists():
        return
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        return

    changed = False

    # Zennデプロイ制限: articles_per_day は最大5本
    apd = config.get("articles_per_day", 2)
    if isinstance(apd, int) and apd > 5:
        logger.warning(
            f"[config.yaml] articles_per_day={apd} はZennデプロイ制限(5本)を超えています → 5に自動修正"
        )
        config["articles_per_day"] = 5
        changed = True

    if changed:
        config_path.write_text(
            yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        notify("⚙️ config.yaml自動修正", "articles_per_day がZennデプロイ制限(5本)を超えていたため5に修正しました。")
        logger.info("[config.yaml] 自動修正完了")


def main():
    parser = argparse.ArgumentParser(description="Zenn自動投稿システム")
    parser.add_argument("--mode", choices=["daily", "weekly"], required=True,
                        help="daily: 日次処理 / weekly: 週次分析")
    args = parser.parse_args()

    _validate_config()

    try:
        if args.mode == "daily":
            run_daily()
        else:
            run_weekly()
    except SystemExit as e:
        if str(e) == "COST_LIMIT_EXCEEDED":
            notify("🛑 月間APIコスト上限", "今月の自動実行を停止します。来月1日に自動再開します。", urgent=True)
            sys.exit(0)
        raise


if __name__ == "__main__":
    main()
