"""
empire/report_generator.py — 実行レポート自動生成 & メール送信

使い方:
    collector = ReportCollector(mode="daily")
    collector.add_action("トレンドスキャン実行")
    collector.add_success("記事生成: 副業節税タイトル")
    collector.add_failure("note投稿失敗", cause="422 Unprocessable Entity", needs_action=True)
    collector.finalize()   # レポート生成 + メール送信
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
        """レポートを生成してファイルに保存し、メール送信する"""
        report_text = self._build_report()
        self.report_path = self._save(report_text)
        self._send_email(report_text)
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

    def _send_email(self, report_text: str) -> None:
        gmail_address = os.environ.get("GMAIL_ADDRESS", "")
        app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
        notify_email = os.environ.get("NOTIFY_EMAIL", "") or gmail_address

        if not gmail_address or not app_password:
            logger.info("[Report] GMAIL_ADDRESS/GMAIL_APP_PASSWORD 未設定 — メール送信スキップ")
            return

        now = datetime.now(timezone.utc)
        jst_hour = (now.hour + 9) % 24
        timestamp = f"{now.strftime('%Y-%m-%d')} {jst_hour:02d}:{now.strftime('%M')}"

        # メインの処理を件名に
        if self._successes:
            main_action = self._successes[0][:30]
        elif self._actions:
            main_action = self._actions[0][:30]
        else:
            main_action = f"{self.mode}処理"

        failures_exist = bool(self._failures)
        subject = f"【帝国レポート】{timestamp} {main_action}{'⚠️要確認' if failures_exist else ''}"

        try:
            msg = MIMEMultipart()
            msg["From"] = gmail_address
            msg["To"] = notify_email
            msg["Subject"] = subject
            msg.attach(MIMEText(report_text, "plain", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(gmail_address, app_password)
                server.sendmail(gmail_address, notify_email, msg.as_string())

            logger.info("[Report] メール送信完了: %s → %s", subject[:60], notify_email)
        except Exception as exc:
            logger.warning("[Report] メール送信失敗: %s", exc)


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
