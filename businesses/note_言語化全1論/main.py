"""
note有料マガジン事業 — main.py
言語化×全1論を毎日1ジャンルに翻訳して投稿する

Usage:
  python businesses/note_言語化全1論/main.py --mode daily
  python businesses/note_言語化全1論/main.py --mode weekly
"""
import argparse
import logging
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml

from empire.playbooks.note_magazine import setup, run, report

BUSINESS_DIR = _HERE
CONFIG_PATH = BUSINESS_DIR / "config.yaml"
VOICE_OS_PATH = _PROJECT_ROOT / "thoughts" / "voice_os.md"
HUMAN_WRITING_OS_PATH = _PROJECT_ROOT / "thoughts" / "human_writing_os.md"
INBOX_PATH = _PROJECT_ROOT / "thoughts" / "inbox.md"

# logs ディレクトリが存在しない場合（GitHub Actions 等）に自動作成
(BUSINESS_DIR / "logs").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(BUSINESS_DIR / "logs" / "main.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    # 著者OSと思考シードを注入
    if VOICE_OS_PATH.exists():
        cfg["voice_os"] = VOICE_OS_PATH.read_text(encoding="utf-8")
    if HUMAN_WRITING_OS_PATH.exists():
        cfg["human_writing_os"] = HUMAN_WRITING_OS_PATH.read_text(encoding="utf-8")
    if INBOX_PATH.exists():
        inbox = INBOX_PATH.read_text(encoding="utf-8").strip()
        if inbox and not inbox.startswith("#"):
            cfg["thought_seeds"] = inbox

    # ジャンルローテーション（毎日1ジャンルを循環）
    genres = cfg.get("genre_rotation", [])
    idx = cfg.get("auto_strategy", {}).get("current_genre_index", 0)
    if genres:
        cfg["today_genre"] = genres[idx % len(genres)]
        # インデックスを進める
        cfg.setdefault("auto_strategy", {})["current_genre_index"] = (idx + 1) % len(genres)
        CONFIG_PATH.write_text(
            yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    return cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly", "report"], default="daily")
    args = parser.parse_args()

    config = load_config()
    logger.info(f"[note] 起動 mode={args.mode} genre={config.get('today_genre','')}")

    setup(config, BUSINESS_DIR)

    from empire.report_generator import ReportCollector
    with ReportCollector(args.mode) as rc:
        rc.add_action(f"note有料マガジン事業 起動 mode={args.mode} genre={config.get('today_genre','')}")

        if args.mode == "report":
            result = report(config, BUSINESS_DIR)
            rc.add_success("パフォーマンスレポート生成完了")
        else:
            result = run(config, BUSINESS_DIR, mode=args.mode)

            published = result.get("published", [])
            for rec in published:
                status = rec.get("status", "")
                title = rec.get("title", "")
                if status == "published":
                    rc.add_success(f"note投稿成功: {title}")
                elif status == "draft_ready":
                    rc.add_failure(
                        f"note投稿失敗→メール通知: {title}",
                        cause="note.com API エラー（CSRF/認証）。メールで記事全文を送信済み。",
                        needs_action=False,
                    )

        logger.info(f"[note] 完了: {result}")


if __name__ == "__main__":
    main()
