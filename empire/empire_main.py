"""
empire_main.py: 帝国全体のオーケストレーター

--mode daily          : 全事業の日次処理 + CEO判断
--mode weekly         : Scout（新市場スキャン）+ Launcher（新事業準備）+ CEO週次レポート
--mode monthly        : 月次総括 + 戦略立案（毎月1日に自動実行）
--mode market_analysis: 自律市場分析（週次。weekly_reviewの直前に実行）
--mode feedback       : オーナーフィードバックを市場分析に反映（--feedback "..." と併用）

GitHub Actionsスケジュール:
  毎日   07:00 JST: --mode daily
  毎週月曜 08:30 JST: --mode market_analysis
  毎週月曜 09:00 JST: --mode weekly
  毎月1日 10:00 JST: --mode monthly
"""
import argparse
import logging
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import yaml

from empire.utils import (
    PROJECT_ROOT, EMPIRE_DIR,
    get_empire_cost_limit, get_empire_month_cost,
    load_portfolio, save_portfolio,
    notify, solve_with_os,
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


# ── リトライ付き実行 ──────────────────────────────────────────────────────────

def _run(func, name: str):
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func()
        except SystemExit:
            raise
        except Exception as e:
            last_exc = e
            logger.error(f"[{name}] エラー (試行 {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT)

    # 全リトライ失敗 — ミリテク思考OS で自己診断
    assert last_exc is not None
    try:
        sol = solve_with_os(problem=f"[{name}] {last_exc}")
        action  = sol.get("action", "—")
        owner   = sol.get("needs_owner", True)
        conf    = sol.get("confidence", 0)
        root    = sol.get("root_cause", "—")
        solution= sol.get("solution", "—")
        flag    = "🔴 オーナー確認が必要" if owner else f"🟡 確信度{conf}% — 自動対処"
        notify(
            f"⚠️ [{name}] {MAX_RETRIES}回失敗 → 思考OS診断",
            f"根本原因: {root}\n解決策: {solution}\n次の一手: {action}\n{flag}",
            urgent=True,
        )
    except Exception:
        notify(f"⚠️ [{name}] {MAX_RETRIES}回失敗", f"エラー: {last_exc}", urgent=True)

    raise last_exc


# ── 安全装置: 帝国コスト上限 ─────────────────────────────────────────────────

def _empire_cost_guard() -> bool:
    """帝国エージェント（CEO/Scout/Launcher）のコストが上限の20%を超えたら停止"""
    empire_cost = get_empire_month_cost()
    limit = get_empire_cost_limit()
    threshold = limit * 0.20  # 帝国エージェント自体のコスト上限は全体の20%
    if empire_cost >= threshold:
        notify(
            "🛑 [帝国] エージェントAPIコスト上限",
            f"エージェントのAPIコスト（¥{empire_cost:.0f}）が上限（¥{threshold:.0f}）に達しました。",
            urgent=True,
        )
        return True
    # 80%超え警告
    if empire_cost >= threshold * 0.80:
        notify(
            "⚠️ [帝国] コスト警告",
            f"エージェントのAPIコスト（¥{empire_cost:.0f}）が上限の80%を超えました（上限: ¥{threshold:.0f}）。",
            urgent=True,
        )
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

    # 月次Slackレポート（先月比 + トップ3 + 収益目標進捗）
    total_rev = float(empire_kpi.get("total_monthly_revenue", 0))
    total_cost = float(empire_kpi.get("total_monthly_cost", 0))
    total_profit = total_rev - total_cost

    # 事業トップ3（収益順）
    sorted_biz = sorted(businesses, key=lambda b: float(b.get("monthly_revenue", 0)), reverse=True)
    top3_lines = "\n".join(
        f"  {i+1}. {b['id']}: ¥{float(b.get('monthly_revenue',0)):,.0f} (ROI {float(b.get('roi',0)):.1f}%)"
        for i, b in enumerate(sorted_biz[:3])
    ) or "  （データなし）"

    # 来月の目標（3ヶ月目標: 30,000円 / 6ヶ月: 100,000円）
    next_target = 30000  # デフォルト3ヶ月目標
    pages_url = "https://blackINT400.github.io/note-auto-revenue/"

    notify(
        f"📅 {target_year}年{target_month}月 帝国月次レポート",
        f"**今月の実績**\n"
        f"収益: ¥{total_rev:,.0f} / コスト: ¥{total_cost:,.0f} / 純利益: ¥{total_profit:,.0f}\n"
        f"稼働事業数: {empire_kpi.get('business_count', 0)}件\n\n"
        f"**パフォーマンストップ3**\n{top3_lines}\n\n"
        f"**来月の目標**: ¥{next_target:,.0f}（あと ¥{max(0, next_target - total_rev):,.0f}）\n\n"
        f"ダッシュボード: {pages_url}\n"
        f"詳細ログ: `{output_path.name}`",
    )


# ── モード別処理 ──────────────────────────────────────────────────────────────

def run_daily():
    logger.info("=" * 60)
    logger.info("帝国 日次処理 開始")
    logger.info("=" * 60)

    from empire.report_generator import ReportCollector
    with ReportCollector("daily") as rc:
        rc.add_action("帝国オーケストレーター 日次処理 開始")

        portfolio = load_portfolio()

        # ── フェーズ自動判定 ──────────────────────────────────────────────────────
        from empire.phase_manager import detect_phase, phase_report
        phase_info = detect_phase(portfolio, PROJECT_ROOT)
        logger.info(phase_info["message"])
        rc.add_action(f"フェーズ判定: {phase_info['name']} "
                      f"（記事{phase_info['article_count']}本 / 月収¥{phase_info['monthly_revenue']:.0f}）")

        if phase_info["phase"] == 0:
            logger.info(
                "[帝国] %s — 無料コンテンツ量産フェーズ。有料機能はブロック済み。",
                phase_info["name"],
            )
        notify(
            f"[帝国] {phase_info['name']}",
            f"記事: {phase_info['article_count']} 本 | 月収: ¥{phase_info['monthly_revenue']:.0f}\n"
            f"{phase_info['next_hint']}",
        )

        # 安全装置: コスト上限チェック（全事業合計）
        cost_limit = get_empire_cost_limit()
        total_biz_cost = sum(float(b.get("monthly_cost", 0)) for b in portfolio.get("businesses", []))
        if total_biz_cost >= cost_limit:
            notify(
                "🛑 [帝国] 月間コスト上限到達",
                f"全事業の累計コスト ¥{total_biz_cost:,.0f} が上限 ¥{cost_limit:,.0f} に達しました。日次処理を停止します。",
                urgent=True,
            )
            logger.error("帝国コスト上限超過。日次処理を中断します。")
            rc.add_failure(
                "コスト上限超過で日次処理を停止",
                cause=f"累計コスト ¥{total_biz_cost:,.0f} ≥ 上限 ¥{cost_limit:,.0f}",
                needs_action=True,
            )
            rc.add_confirmation(f"月間APIコストが上限（¥{cost_limit:,.0f}）に達しました。上限引き上げまたは処理削減を検討してください。")
            return
        # 80%警告
        elif total_biz_cost >= cost_limit * 0.80:
            notify(
                "⚠️ [帝国] コスト警告 80%超え",
                f"今月のコスト ¥{total_biz_cost:,.0f}（上限 ¥{cost_limit:,.0f} の{total_biz_cost/cost_limit*100:.0f}%）",
                urgent=True,
            )

        # 全アクティブ事業を実行（フェーズに関係なく日次生成は常に実行）
        rc.add_action("全アクティブ事業の日次処理を実行")
        _run_all_businesses_daily(portfolio)
        active_biz = [b["id"] for b in portfolio.get("businesses", []) if b.get("status") == "active"]
        rc.add_success(f"事業日次処理完了: {', '.join(active_biz)}")

        # CEO判断
        if not _empire_cost_guard():
            rc.add_action("CEO エージェント 日次判断")
            try:
                from empire import ceo_agent
                _run(lambda: ceo_agent.run(notify_fn=notify, weekly_report=False), "CEO（日次判断）")
                rc.add_success("CEO 日次判断完了")
            except Exception as e:
                rc.add_failure("CEO 日次判断エラー", cause=str(e)[:100])

        # ── KPIデータ収集 & ダッシュボード更新 ──────────────────────────────────
        snapshot = {}
        try:
            from dashboard.collector import collect as collect_kpi
            from dashboard.generator import generate as generate_dashboard
            rc.add_action("KPI収集 & ダッシュボード更新")
            snapshot = collect_kpi()
            generate_dashboard(snapshot)
            logger.info("[帝国] ダッシュボード更新完了")
            rc.add_success("ダッシュボード更新完了")
        except Exception as e:
            logger.warning(f"[帝国] ダッシュボード更新スキップ: {e}")
            rc.add_failure("ダッシュボード更新スキップ", cause=str(e)[:100])

        # ── Discord日次サマリー ──────────────────────────────────────────────────
        total = snapshot.get("total", {})
        rev = float(total.get("revenue", 0))
        cost = float(total.get("cost", 0))
        profit = float(total.get("profit", 0))
        roi = float(total.get("roi", 0))
        pages_url = "https://blackINT400.github.io/note-auto-revenue/"
        notify(
            f"✅ [帝国] 日次処理完了 {date.today()}",
            f"今月の収益: ¥{rev:,.0f} / コスト: ¥{cost:,.0f} / 純利益: ¥{profit:,.0f} (ROI {roi:.1f}%)\n"
            f"ダッシュボード: {pages_url}",
        )
        logger.info("帝国 日次処理 完了")
        rc.add_action("日次処理完了")


def run_market_analysis() -> dict:
    """市場分析エージェント単独実行（weekly の直前に呼ばれる）"""
    logger.info("=" * 60)
    logger.info("帝国 市場分析 開始")
    logger.info("=" * 60)

    from empire.report_generator import ReportCollector
    with ReportCollector("market_analysis") as rc:
        rc.add_action("自律市場分析エージェント 開始")

        try:
            from empire import market_analyst
            result = _run(market_analyst.run, "MarketAnalyst（市場分析）")
            analysis = result.get("analysis", {}) if result else {}
            top = (analysis.get("top_opportunities") or [""])[:1]
            rc.add_success(f"市場分析完了: トップ機会「{top[0] if top else '—'}」")
            logger.info("帝国 市場分析 完了")
            return result or {}
        except Exception as e:
            rc.add_failure("市場分析エラー", cause=str(e)[:100])
            logger.warning(f"[帝国] 市場分析エラー（後続処理は継続）: {e}")
            return {}


def run_weekly():
    logger.info("=" * 60)
    logger.info("帝国 週次処理 開始")
    logger.info("=" * 60)

    from empire.report_generator import ReportCollector
    with ReportCollector("weekly") as rc:
        rc.add_action("帝国オーケストレーター 週次処理 開始")

        if _empire_cost_guard():
            logger.warning("帝国エージェントのコスト上限により週次処理をスキップします")
            rc.add_failure("週次処理スキップ", cause="帝国エージェントのAPIコスト上限超過", needs_action=True)
            rc.add_confirmation("帝国エージェントのAPIコストが上限の20%を超えています。週次スカウト・CEO判断を手動で確認してください。")
            return

        portfolio = load_portfolio()

        # 市場分析（Scout前に実行して結果を統合）
        rc.add_action("MarketAnalyst エージェント: 自律市場分析")
        market_result = {}
        try:
            from empire import market_analyst
            market_result = _run(market_analyst.run, "MarketAnalyst（市場分析）")
            market_result = market_result or {}
            top = (market_result.get("analysis", {}).get("top_opportunities") or [""])[:1]
            rc.add_success(f"市場分析完了: トップ機会「{top[0] if top else '—'}」")
        except Exception as e:
            rc.add_failure("市場分析エラー（後続処理は継続）", cause=str(e)[:100])

        # Scout: 新市場スキャン
        rc.add_action("Scout エージェント: 新市場スキャン")
        try:
            from empire import scout_agent
            opportunities = _run(scout_agent.run, "Scout（新市場スキャン）")
            rc.add_success(f"市場スキャン完了: {len(opportunities or [])} 件の機会を発見")
        except Exception as e:
            rc.add_failure("市場スキャン失敗", cause=str(e)[:100])
            opportunities = []

        # Launcher: 新事業準備（候補があれば）
        if opportunities:
            rc.add_action("Launcher エージェント: 新事業準備")
            try:
                from empire import launcher_agent
                new_bid = _run(lambda: launcher_agent.run(opportunities, notify_fn=notify), "Launcher（新事業準備）")
                if new_bid:
                    logger.info(f"[帝国] 新事業「{new_bid}」の準備完了。Slackで承認を確認してください。")
                    rc.add_success(f"新事業準備完了: {new_bid}")
                    rc.add_confirmation(f"新事業「{new_bid}」の起動承認が必要です。portfolio.yaml の status を 'active' に変更してください。")
            except Exception as e:
                rc.add_failure("新事業準備失敗", cause=str(e)[:100])

        # CEO: 週次レポート付き判断（market_result を渡して活用）
        rc.add_action("CEO エージェント: 週次判断 + レポート")
        try:
            from empire import ceo_agent
            _run(lambda: ceo_agent.run(notify_fn=notify, weekly_report=True), "CEO（週次判断 + レポート）")
            rc.add_success("CEO 週次判断完了")
        except Exception as e:
            rc.add_failure("CEO 週次判断エラー", cause=str(e)[:100])

        # 市場分析セクションを週次レポートに追記
        if market_result:
            try:
                from empire.market_analyst import format_discord_section
                import json as _json
                learnings_path = Path(__file__).parent.parent / "owner" / "market_learnings.json"
                learnings = {}
                if learnings_path.exists():
                    learnings = _json.loads(learnings_path.read_text(encoding="utf-8"))
                section = format_discord_section(market_result.get("analysis", {}), learnings)
                if section:
                    from empire.utils import notify
                    notify("🔬 市場分析レポート（週次）", section)
            except Exception as e:
                logger.warning(f"[帝国] 市場分析Discord通知スキップ: {e}")

        logger.info("帝国 週次処理 完了")


def run_monthly():
    logger.info("=" * 60)
    logger.info("帝国 月次総括 開始")
    logger.info("=" * 60)

    from empire.report_generator import ReportCollector
    with ReportCollector("monthly") as rc:
        rc.add_action("帝国オーケストレーター 月次総括 開始")

        portfolio = load_portfolio()

        try:
            _run(lambda: run_monthly_summary(portfolio), "月次総括")
            rc.add_success("月次サマリー生成・Slack送信完了")
        except Exception as e:
            rc.add_failure("月次サマリー生成失敗", cause=str(e)[:100])

        # ── 月次ダッシュボード再生成（フルリビルド）───────────────────────────────
        try:
            from dashboard.collector import collect as collect_kpi
            from dashboard.generator import generate as generate_dashboard
            rc.add_action("月次ダッシュボード再生成")
            snapshot = collect_kpi()
            generate_dashboard(snapshot)
            logger.info("[帝国] 月次ダッシュボード再生成完了")
            rc.add_success("月次ダッシュボード再生成完了")
        except Exception as e:
            logger.warning(f"[帝国] 月次ダッシュボード生成スキップ: {e}")
            rc.add_failure("月次ダッシュボード生成スキップ", cause=str(e)[:100])

        logger.info("帝国 月次総括 完了")


# ── エントリーポイント ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="帝国オーケストレーター")
    parser.add_argument(
        "--mode",
        choices=["daily", "weekly", "monthly", "market_analysis", "feedback"],
        required=True,
        help=(
            "daily: 日次処理 / weekly: スカウト+ポートフォリオ最適化 / monthly: 月次総括 / "
            "market_analysis: 自律市場分析 / feedback: 市場分析フィードバック反映"
        ),
    )
    parser.add_argument(
        "--feedback",
        default="",
        help="--mode feedback 時のフィードバックテキスト（例: '修正: コンテンツ販売に集中すべき'）",
    )
    args = parser.parse_args()

    try:
        if args.mode == "daily":
            run_daily()
        elif args.mode == "weekly":
            run_weekly()
        elif args.mode == "monthly":
            run_monthly()
        elif args.mode == "market_analysis":
            run_market_analysis()
        elif args.mode == "feedback":
            if not args.feedback:
                print("--feedback テキストを指定してください。例: --feedback '修正: コンテンツ販売優先'")
                sys.exit(1)
            from empire.market_analyst import apply_feedback
            result = apply_feedback(args.feedback)
            print(result)
    except SystemExit as e:
        if str(e) == "COST_LIMIT_EXCEEDED":
            notify("🛑 [帝国] APIコスト上限", "今月のAPIコスト上限に達しました。処理を停止します。", urgent=True)
            sys.exit(0)
        raise


if __name__ == "__main__":
    main()
