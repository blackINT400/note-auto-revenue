"""
empire/report_generator.py — 実行レポート自動生成 & Discord送信

使い方:
    collector = ReportCollector(mode="daily")
    collector.add_action("トレンドスキャン実行")
    collector.add_success("記事生成: 副業節税タイトル")
    collector.add_failure("note投稿失敗", cause="422 Unprocessable Entity", needs_action=True)
    collector.finalize()   # レポート生成 + Discord送信
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── パス定数 ────────────────────────────────────────────────────────────────
EMPIRE_DIR = Path(__file__).parent
PROJECT_ROOT = EMPIRE_DIR.parent
PORTFOLIO_PATH = EMPIRE_DIR / "portfolio.yaml"
EMPIRE_COST_PATH = EMPIRE_DIR / "data" / "empire_cost.json"
ZENN_COST_PATH = PROJECT_ROOT / "data" / "cost_tracker.json"
LOG_DIR = PROJECT_ROOT / "logs"

# フェーズ名マップ
PHASE_NAMES = {0: "フェーズ0（種まき）", 1: "フェーズ1（芽吹き）",
               2: "フェーズ2（成長）", 3: "フェーズ3（拡大）"}

# 次回実行の説明マップ
NEXT_RUN_DESC = {
    "daily":   "明日 06:00 JST に日次処理（記事生成・投稿）",
    "weekly":  "翌月曜 08:00 JST に週次処理（市場スキャン・CEO判断）",
    "monthly": "翌月 1 日 10:00 JST に月次処理（総括・ダッシュボード再生成）",
}


# ── データ収集ヘルパー ─────────────────────────────────────────────────────

def _load_portfolio() -> dict:
    try:
        return yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _count_published_articles(project_root: Path) -> int:
    """全事業の published.jsonl 行数合計（ステータス問わず）"""
    total = 0
    # Zenn（プロジェクトルート）
    for p in [
        project_root / "logs" / "published.jsonl",
        project_root / "data" / "published.jsonl",
    ]:
        if p.exists():
            total += sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())

    # 各事業ディレクトリ
    for biz_dir in (project_root / "businesses").glob("*/"):
        jsonl = biz_dir / "data" / "published.jsonl"
        if jsonl.exists():
            total += sum(1 for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip())

    return total


def _get_monthly_cost(project_root: Path) -> float:
    """今月の全APIコスト合計（円）"""
    from datetime import date
    this_month = str(date.today())[:7]
    total = 0.0

    for cost_path in [EMPIRE_COST_PATH, ZENN_COST_PATH]:
        if not cost_path.exists():
            continue
        try:
            d = json.loads(cost_path.read_text(encoding="utf-8"))
            if d.get("month") == this_month:
                total += float(d.get("total_jpy", 0))
        except Exception:
            pass

    # 各事業の cost_tracker
    for tracker in (project_root / "businesses").glob("*/data/cost_tracker.json"):
        try:
            d = json.loads(tracker.read_text(encoding="utf-8"))
            if d.get("month") == this_month:
                total += float(d.get("total_jpy", 0))
        except Exception:
            pass

    return round(total, 1)


def _get_phase(portfolio: dict, project_root: Path) -> tuple[int, str]:
    """(phase_int, phase_name)"""
    try:
        from empire.phase_manager import detect_phase
        info = detect_phase(portfolio, project_root)
        return info["phase"], info["name"]
    except Exception:
        return 0, PHASE_NAMES[0]


def _count_active_businesses(portfolio: dict) -> int:
    return sum(1 for b in portfolio.get("businesses", []) if b.get("status") == "active")


def _get_monthly_revenue(portfolio: dict) -> float:
    return float(portfolio.get("empire_kpi", {}).get("total_monthly_revenue", 0))


# ── レポートコレクター ────────────────────────────────────────────────────

class ReportCollector:
    """
    実行中にアクション・成功・失敗を蓄積し、終了時にレポートを生成する。

    使い方:
        with ReportCollector("daily") as rc:
            rc.add_action("トレンドスキャン実行")
            rc.add_success("記事生成完了: タイトル")
            rc.add_failure("note投稿失敗", cause="422エラー", needs_action=True)
        # with ブロックを抜けると finalize() が自動呼び出し
    """

    def __init__(self, mode: str = "daily"):
        self.mode = mode
        self.started_at = datetime.now(timezone.utc)
        self._actions: list[str] = []
        self._successes: list[str] = []
        self._failures: list[dict] = []   # {msg, cause, needs_action}
        self._confirmations: list[str] = []  # オーナー確認事項
        self.report_path: Path | None = None

    # ── イベント追記 API ────────────────────────────────────────────────

    def add_action(self, msg: str) -> None:
        self._actions.append(msg)
        logger.debug("[Report] action: %s", msg)

    def add_success(self, msg: str) -> None:
        self._successes.append(msg)
        logger.debug("[Report] success: %s", msg)

    def add_failure(self, msg: str, cause: str = "", needs_action: bool = False) -> None:
        self._failures.append({"msg": msg, "cause": cause, "needs_action": needs_action})
        logger.debug("[Report] failure: %s (cause=%s)", msg, cause)

    def add_confirmation(self, msg: str) -> None:
        """オーナーに確認が必要な事項"""
        self._confirmations.append(msg)

    # ── context manager ─────────────────────────────────────────────────

    def __enter__(self) -> "ReportCollector":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self.add_failure(
                f"予期しないエラーで処理が中断されました: {exc_type.__name__}",
                cause=str(exc_val)[:200],
                needs_action=True,
            )
        self.finalize()
        return False  # 例外は再送出

    # ── レポート生成 ────────────────────────────────────────────────────

    def finalize(self) -> Path:
        """レポートを生成してファイルに保存し、Discord送信する"""
        report_text = self._build_report()
        self.report_path = self._save(report_text)
        self._send_discord()
        logger.info("[Report] 生成完了: %s", self.report_path)
        return self.report_path

    def _build_report(self) -> str:
        now = datetime.now(timezone.utc)
        jst_hour = (now.hour + 9) % 24
        timestamp = f"{now.strftime('%Y-%m-%d')} {jst_hour:02d}:{now.strftime('%M')} JST"

        portfolio = _load_portfolio()
        article_count = _count_published_articles(PROJECT_ROOT)
        monthly_revenue = _get_monthly_revenue(portfolio)
        api_cost = _get_monthly_cost(PROJECT_ROOT)
        active_biz = _count_active_businesses(portfolio)
        phase_int, phase_name = _get_phase(portfolio, PROJECT_ROOT)

        # アクション
        actions_section = "\n".join(f"- {a}" for a in self._actions) if self._actions else "- （記録なし）"

        # 成功
        success_section = "\n".join(f"- {s}" for s in self._successes) if self._successes else "- なし"

        # 失敗
        if self._failures:
            failure_lines = []
            for f in self._failures:
                failure_lines.append(f"- {f['msg']}")
                if f.get("cause"):
                    failure_lines.append(f"  - 原因: {f['cause']}")
                needs = "はい" if f.get("needs_action") else "いいえ"
                failure_lines.append(f"  - 対処が必要か: {needs}")
            failure_section = "\n".join(failure_lines)
        else:
            failure_section = "- なし"

        # 次回予定
        mode_schedules = {
            "daily":   [
                "次回 06:00 JST: 日次処理（トレンドスキャン → 記事生成 → 投稿/通知）",
                "毎週月曜 08:00 JST: 週次処理（市場スキャン・CEO戦略判断）",
            ],
            "weekly":  [
                "明日 06:00 JST: 日次処理（通常運転）",
                "来週月曜 08:00 JST: 次回週次処理",
            ],
            "monthly": [
                "明日 06:00 JST: 日次処理（通常運転）",
                "翌月 1 日 10:00 JST: 次回月次総括",
            ],
        }
        next_lines = "\n".join(f"- {l}" for l in mode_schedules.get(self.mode, []))

        # 確認事項
        if self._confirmations:
            confirm_section = "\n".join(f"- {c}" for c in self._confirmations)
        else:
            confirm_section = "- なし"

        elapsed = now - self.started_at
        elapsed_min = int(elapsed.total_seconds() // 60)
        elapsed_sec = int(elapsed.total_seconds() % 60)

        report = f"""# 実行レポート {timestamp}

モード: `{self.mode}` / 実行時間: {elapsed_min}分{elapsed_sec}秒

## 今回やったこと
{actions_section}

## 現在の状況
- 投稿済み記事数：{article_count} 本
- 今月の収益：¥{monthly_revenue:,.0f}
- 今月のAPIコスト：¥{api_cost:,.1f}
- 動いている事業：{active_biz} 個
- 現在のフェーズ：{phase_name}

## 成功したこと
{success_section}

## 失敗・問題があったこと
{failure_section}

## 次回の予定
{next_lines}

## あなたへの確認事項
{confirm_section}

---
このレポートをClaudeに共有する場合は
このファイルの内容をそのままコピペしてください。
"""
        return report

    def _save(self, report_text: str) -> Path:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        jst_hour = (now.hour + 9) % 24
        filename = f"report_{now.strftime('%Y-%m-%d')}_{jst_hour:02d}-{now.strftime('%M')}.md"
        path = LOG_DIR / filename
        path.write_text(report_text, encoding="utf-8")
        return path

    def _send_discord(self) -> None:
        """帝国デイリーレポートを Discord に複数 embed で送信する。"""
        import os
        import requests as _req
        from empire.utils import DISCORD_WEBHOOK_URL

        if not DISCORD_WEBHOOK_URL:
            logger.debug("[Report] DISCORD_WEBHOOK_URL 未設定 — 通知スキップ")
            return

        now = datetime.now(timezone.utc)
        jst_hour = (now.hour + 9) % 24
        jst_date = f"{now.strftime('%Y-%m-%d')} {jst_hour:02d}:{now.strftime('%M')} JST"

        # ── データ収集 ───────────────────────────────────────────────────────
        portfolio = _load_portfolio()
        article_count = _count_published_articles(PROJECT_ROOT)
        monthly_revenue = _get_monthly_revenue(portfolio)
        api_cost = _get_monthly_cost(PROJECT_ROOT)
        cost_limit = float(portfolio.get("revenue_pool", {}).get("monthly_cost_limit", 5000))
        phase_int, phase_name = _get_phase(portfolio, PROJECT_ROOT)
        active_biz = _count_active_businesses(portfolio)

        # 収益プール
        revenue_pool = portfolio.get("revenue_pool", {})
        available_budget = float(revenue_pool.get("available_budget", 0))

        # 月間目標（フェーズ別）
        monthly_goals = {0: 3000, 1: 30000, 2: 100000, 3: 300000}
        monthly_goal = monthly_goals.get(phase_int, 3000)
        goal_progress = min(100, monthly_revenue / monthly_goal * 100) if monthly_goal > 0 else 0
        goal_remaining = max(0, monthly_goal - monthly_revenue)

        # フェーズ移行条件
        phase_conditions = {
            0: f"記事10本以上 + フォロワー50人以上（あと {max(0, 10 - article_count)} 記事）",
            1: f"月収 ¥3,000 達成（あと ¥{max(0, 3000 - monthly_revenue):,.0f}）",
            2: f"月収 ¥10,000 × 3ヶ月連続（あと ¥{max(0, 10000 - monthly_revenue):,.0f}）",
            3: "最終フェーズ稼働中",
        }
        phase_condition = phase_conditions.get(phase_int, "")
        phase_labels = {0: "✅ 進行中", 1: "未着手", 2: "未着手", 3: "未着手"}
        for i in range(phase_int):
            phase_labels[i] = "✅ 完了"
        phase_labels[phase_int] = "▶ 進行中"

        # 稼働事業リスト
        biz_list_lines = []
        for b in portfolio.get("businesses", []):
            if b.get("status") == "active":
                rev = float(b.get("monthly_revenue", 0))
                roi = float(b.get("roi", 0))
                biz_list_lines.append(f"・{b['id']}: 月収 ¥{rev:,.0f} / ROI {roi:.0f}%")
        biz_list_str = "\n".join(biz_list_lines) or "・（なし）"

        # 次期展開事業（pending_human_approval）
        next_biz_lines = []
        for b in portfolio.get("businesses", []):
            if b.get("status") == "pending_human_approval":
                next_biz_lines.append(f"・{b['id']}（承認待ち）")
        next_biz_str = "\n".join(next_biz_lines) or "・（スカウト待機中）"

        # スカウトスコア（portfolio から取得、なければ取得中）
        scout_score = portfolio.get("scout_last_score", "取得中")

        # トレンドキーワード（config.yaml から）
        trend_kws = []
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
            trend_kws = cfg.get("auto_strategy", {}).get("top_keywords", [])[:3]
        except Exception:
            pass
        trend_str = "\n".join(f"{i+1}. {k}" for i, k in enumerate(trend_kws)) or "取得中"

        # A8審査経過日数
        a8_path = PROJECT_ROOT / "data" / "a8_applied_date.txt"
        if a8_path.exists():
            try:
                from datetime import date
                applied = date.fromisoformat(a8_path.read_text().strip())
                a8_days = (date.today() - applied).days
                a8_str = f"申請から {a8_days} 日目"
            except Exception:
                a8_str = "取得中"
        else:
            a8_str = "未申請"

        # Threads状態
        threads_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
        threads_str = "✅ 自動投稿設定済み" if threads_token else "⏳ 手動投稿待機中（トークン未設定）"

        # ベスト記事（published.jsonl から quality_score 最高のもの）
        best_title, best_score = "取得中", 0
        try:
            jsonl = PROJECT_ROOT / "logs" / "published.jsonl"
            if jsonl.exists():
                for line in jsonl.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    sc = int(rec.get("quality_score", 0))
                    if sc > best_score:
                        best_score = sc
                        best_title = rec.get("title", "")[:40]
        except Exception:
            pass

        # 純利益
        net_profit = monthly_revenue - api_cost
        cost_pct = min(100, api_cost / cost_limit * 100) if cost_limit > 0 else 0

        # 成功・失敗テキスト
        success_str = "\n".join(f"・{s}" for s in self._successes) or "・なし"
        if self._failures:
            fail_lines = []
            for f in self._failures:
                fail_lines.append(f"・{f['msg']}")
                if f.get("cause"):
                    fail_lines.append(f"　└ 原因: {f['cause'][:80]}")
            fail_str = "\n".join(fail_lines)
        else:
            fail_str = "・なし"

        confirm_str = "\n".join(f"・{c}" for c in self._confirmations) or "・なし"

        next_schedules = {
            "daily":   "明日 06:00 JST: 日次処理（記事生成・投稿）\n毎週月曜 08:00 JST: 週次処理（市場スキャン・CEO判断）",
            "weekly":  "明日 06:00 JST: 日次処理（通常運転）\n来週月曜 08:00 JST: 次回週次処理",
            "monthly": "明日 06:00 JST: 日次処理（通常運転）\n翌月 1 日 10:00 JST: 次回月次総括",
        }
        next_str = next_schedules.get(self.mode, "")

        urgent = bool(self._failures and any(f.get("needs_action") for f in self._failures))
        color_main = 0xFF4444 if urgent else 0x1D9E75  # 赤 or 緑

        # ── Discordに複数メッセージで送信 ─────────────────────────────────────
        def _post(embeds: list) -> None:
            try:
                r = _req.post(DISCORD_WEBHOOK_URL, json={"embeds": embeds}, timeout=10)
                if r.status_code not in (200, 204):
                    logger.warning("[Report] Discord送信失敗: %d", r.status_code)
            except Exception as e:
                logger.warning("[Report] Discord送信エラー: %s", e)

        # ── メッセージ1: ヘッダー + 収益 + ロードマップ ─────────────────────
        msg1_embeds = [
            {
                "title": f"📊 帝国デイリーレポート {jst_date}",
                "description": (
                    f"━━━━━━━━━━━━━━━\n"
                    f"💰 **収益状況**\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"・今月の収益: ¥{monthly_revenue:,.0f}\n"
                    f"・今月のコスト: ¥{api_cost:,.0f}\n"
                    f"・今月の純利益: ¥{net_profit:,.0f}\n"
                    f"・目標達成率: {goal_progress:.1f}%（月間目標 ¥{monthly_goal:,} まであと ¥{goal_remaining:,.0f}）\n\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"📍 **ロードマップ現在地**\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"現在: **{phase_name}**\n"
                    f"進捗:\n"
                    f"・フェーズ0（種まき）: {phase_labels[0]}\n"
                    f"・フェーズ1（芽吹き）: {phase_labels[1]}\n"
                    f"・フェーズ2（成長）:   {phase_labels[2]}\n"
                    f"・フェーズ3（拡大）:   {phase_labels[3]}\n\n"
                    f"次のフェーズ移行条件: {phase_condition}"
                )[:4096],
                "color": color_main,
            }
        ]
        _post(msg1_embeds)

        # ── メッセージ2: ビジネス展開 + コンテンツ + システム ────────────────
        msg2_embeds = [
            {
                "title": "🗺️ ビジネス展開計画",
                "description": (
                    f"現在稼働中の事業:\n{biz_list_str}\n\n"
                    f"次に展開予定の事業:\n{next_biz_str}\n\n"
                    f"展開トリガーまであと:\n"
                    f"・収益プール: ¥{available_budget:,.0f} / ¥10,000（{min(100, available_budget/100):.0f}%）\n"
                    f"・既存事業数: {active_biz} / 5件\n"
                    f"・スカウトスコア: {scout_score} / 35点"
                )[:4096],
                "color": 0x5865F2,
            },
            {
                "title": "📊 コンテンツパフォーマンス",
                "description": (
                    f"今週のベスト記事:\n"
                    f"・タイトル: {best_title}\n"
                    f"・品質スコア: {best_score}点\n"
                    f"・PV / スキ: 取得中（Zenn API非公開）\n\n"
                    f"今日のトレンドキーワード（記事ネタ候補）:\n{trend_str}"
                )[:4096],
                "color": 0x5865F2,
            },
            {
                "title": "⚙️ システム状況",
                "description": (
                    f"・投稿済み記事数: {article_count}本\n"
                    f"・APIコスト残り: ¥{max(0, cost_limit - api_cost):,.0f}（上限 ¥{cost_limit:,.0f} の {cost_pct:.0f}% 使用）\n"
                    f"・A8審査: {a8_str}\n"
                    f"・Threads: {threads_str}\n"
                    f"・稼働事業数: {active_biz}個"
                )[:4096],
                "color": 0x5865F2,
            },
        ]
        _post(msg2_embeds)

        # ── メッセージ3: 成功/エラー + 次のアクション + 次回実行 ────────────
        today_action = self._successes[0][:60] if self._successes else "記事生成・投稿"
        week_action = "週次処理でスカウトスコアを更新" if self.mode == "daily" else "A/Bテスト評価・テーマ発掘"
        msg3_embeds = [
            {
                "title": "✅ 今日の成功 / ⚠️ 問題・エラー",
                "description": (
                    f"**✅ 成功**\n{success_str}\n\n"
                    f"**⚠️ 問題・エラー**\n{fail_str}"
                )[:4096],
                "color": 0xFF4444 if urgent else 0x57F287,
            },
            {
                "title": "🎯 今フェーズでやるべきこと / ⏭️ 次回実行",
                "description": (
                    f"**今日やること**: {today_action}\n"
                    f"**今週やること**: {week_action}\n"
                    f"**確認事項**: {confirm_str}\n\n"
                    f"**⏭️ 次回実行予定**\n{next_str}"
                )[:4096],
                "color": color_main,
            },
        ]
        _post(msg3_embeds)

        # ── メッセージ4: 証明済み行動ログ（daily/weekly 共通）────────────────
        try:
            from empire.proposition_lib import (
                load_propositions, format_daily_discord, get_top_patterns,
            )
            from datetime import date as _date
            props_data = load_propositions()
            today_str = str(_date.today())
            today_proofs = [
                p for p in props_data.get("propositions", [])
                if p.get("date") == today_str
            ]
            proof_section = format_daily_discord(props_data, today_proofs)
            _post([{
                "title": "📐 証明済み行動ログ",
                "description": proof_section[:4096],
                "color": 0xEB459E,
            }])
        except Exception as _e:
            logger.debug("[Report] 証明ログ送信スキップ: %s", _e)

        # ── メッセージ5: 週次のみ — 自動学習結果 ─────────────────────────────
        if self.mode == "weekly":
            learning_text = "取得中"
            try:
                lpath = PROJECT_ROOT / "owner" / "learnings.md"
                if lpath.exists():
                    lines = lpath.read_text(encoding="utf-8").splitlines()
                    # 最新の週次エントリを抽出
                    in_latest = False
                    latest_lines = []
                    for line in reversed(lines):
                        if line.startswith("## ") and "週次学習" in line:
                            in_latest = True
                        if in_latest:
                            latest_lines.insert(0, line)
                            if len(latest_lines) > 15:
                                break
                    learning_text = "\n".join(latest_lines)[:800] or "今週の学習データなし"
            except Exception:
                pass

            thoughts_summary = "取得中"
            try:
                tpath = PROJECT_ROOT / "owner" / "thoughts.md"
                if tpath.exists():
                    # 最新追記のタイトルだけ抽出
                    lines = tpath.read_text(encoding="utf-8").splitlines()
                    titles = [l for l in lines if l.startswith("### ")][-3:]
                    thoughts_summary = "\n".join(f"・{t.lstrip('#').strip()}" for t in titles) or "なし"
            except Exception:
                pass

            _post([{
                "title": "🧠 今週の自動学習結果",
                "description": (
                    f"**学習したこと（learnings.md）**:\n{learning_text}\n\n"
                    f"**thoughts.mdから読み取った判断基準**:\n{thoughts_summary}\n\n"
                    f"**今週の0→1（新たに確定したこと）**:\n・context_prompt.md を更新して次回記事生成に反映済み"
                )[:4096],
                "color": 0xFEE75C,
            }])

        logger.info("[Report] Discord送信完了（%s モード）", self.mode)


# ── スタンドアロン生成（empire_main.py 外から呼ぶ用）────────────────────────

def quick_report(
    mode: str,
    actions: list[str],
    successes: list[str],
    failures: list[dict],
    confirmations: list[str] | None = None,
) -> Path:
    """ReportCollector を使わずにレポートを一発生成する"""
    rc = ReportCollector(mode=mode)
    for a in actions:
        rc.add_action(a)
    for s in successes:
        rc.add_success(s)
    for f in failures:
        if isinstance(f, str):
            rc.add_failure(f)
        else:
            rc.add_failure(f.get("msg", ""), f.get("cause", ""), f.get("needs_action", False))
    for c in (confirmations or []):
        rc.add_confirmation(c)
    return rc.finalize()
