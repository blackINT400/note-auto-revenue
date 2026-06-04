"""
note有料マガジン事業 — main.py
言語化×全1論を毎日2ジャンルに翻訳して投稿する

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

import json

import yaml

from empire.playbooks.note_magazine import setup, run, report
from agents import genre_engine

BUSINESS_DIR = _HERE
CONFIG_PATH = BUSINESS_DIR / "config.yaml"
VOICE_OS_PATH = _PROJECT_ROOT / "thoughts" / "voice_os.md"
HUMAN_WRITING_OS_PATH = _PROJECT_ROOT / "thoughts" / "human_writing_os.md"
INBOX_PATH = _PROJECT_ROOT / "thoughts" / "inbox.md"
AFFILIATES_PATH = _PROJECT_ROOT / "owner" / "affiliates.yaml"
PATTERNS_PATH = _PROJECT_ROOT / "owner" / "note_patterns.json"

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
    """
    config.yaml を読み込み、著者OS・ジャンルを注入して返す。
    呼ぶたびにジャンルインデックスが1進む（2回呼ぶと2ジャンル取得できる）。
    """
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

    # ── ジャンル抽象化・具体化エンジンでテーマを決定 ─────────────────────
    genres = cfg.get("genre_rotation", [])
    idx = cfg.get("auto_strategy", {}).get("current_genre_index", 0)
    if genres:
        theme_result = genre_engine.get_next_theme(
            data_dir=BUSINESS_DIR,
            genre_rotation=genres,
            current_idx=idx,
            affiliates_path=AFFILIATES_PATH,
            patterns_path=PATTERNS_PATH,
            client=None,  # API不要のルールベースモード
        )
        cfg["today_genre"] = theme_result["theme"]
        cfg.setdefault("auto_strategy", {})["current_genre_index"] = theme_result["next_genre_index"]
        CONFIG_PATH.write_text(
            yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info(
            "ジャンル選定: %s (level=%d, %s)",
            theme_result["theme"],
            theme_result["level"],
            theme_result["reasoning"],
        )

    return cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly", "report"], default="daily")
    args = parser.parse_args()

    from empire.report_generator import ReportCollector

    # ── weekly / report は従来通り1回実行 ────────────────────────────────────
    if args.mode != "daily":
        config = load_config()
        logger.info(f"[note] 起動 mode={args.mode} genre={config.get('today_genre','')}")
        setup(config, BUSINESS_DIR)
        with ReportCollector(args.mode) as rc:
            rc.add_action(f"note有料マガジン事業 起動 mode={args.mode}")
            if args.mode == "report":
                result = report(config, BUSINESS_DIR)
                rc.add_success("パフォーマンスレポート生成完了")
            else:
                result = run(config, BUSINESS_DIR, mode=args.mode)
        logger.info(f"[note] 完了: {result}")
        return

    # ── daily: 2ジャンル × 1本ずつ生成 ──────────────────────────────────────
    # load_config() を2回呼ぶことでジャンルインデックスを2つ進め、異なるジャンルを取得
    config1, config2 = _load_two_different_configs()

    genre1 = config1.get("today_genre", "")
    genre2 = config2.get("today_genre", "")
    logger.info(f"[note] 起動 mode=daily | 1本目ジャンル={genre1} | 2本目ジャンル={genre2}")

    setup(config1, BUSINESS_DIR)

    with ReportCollector(args.mode) as rc:
        rc.add_action(
            f"note有料マガジン事業 起動 mode=daily "
            f"| 1本目={genre1} | 2本目={genre2}"
        )

        all_published = []

        # 1本目
        result1 = run(config1, BUSINESS_DIR, mode="daily", article_num_offset=0)
        for rec in result1.get("published", []):
            _process_record(rec, rc, config1)
            all_published.append(rec)

        # 2本目（ジャンルを変えて再実行）
        result2 = run(config2, BUSINESS_DIR, mode="daily", article_num_offset=1)
        for rec in result2.get("published", []):
            _process_record(rec, rc, config2)
            all_published.append(rec)

        logger.info(f"[note] 完了: {len(all_published)}本生成")


def _load_two_different_configs() -> tuple[dict, dict]:
    """
    2本目のジャンルが1本目と被らないよう、必要なら強制的に別ジャンルを選ぶ。
    genre_engine は「同テーマ3回まで継続」の設計なので、1日2本生成すると
    同テーマになりやすい。大ジャンル（「・」より前）が同じなら強制ローテーション。
    """
    config1 = load_config()
    config2 = load_config()

    genre1 = config1.get("today_genre", "")
    genre2 = config2.get("today_genre", "")
    genre1_base = genre1.split("・")[0]
    genre2_base = genre2.split("・")[0]

    if genre1_base == genre2_base:
        genres = config2.get("genre_rotation", [])
        current_idx = config2.get("auto_strategy", {}).get("current_genre_index", 0)

        # genre1 と大ジャンルが違うものを探す
        for offset in range(1, len(genres)):
            candidate_idx = (current_idx + offset) % len(genres)
            candidate = genres[candidate_idx]
            candidate_base = candidate.split("・")[0]
            if candidate_base != genre1_base:
                config2["today_genre"] = candidate
                config2.setdefault("auto_strategy", {})["current_genre_index"] = (
                    (candidate_idx + 1) % len(genres)
                )
                CONFIG_PATH.write_text(
                    yaml.dump(config2, allow_unicode=True, default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
                logger.info("2本目ジャンルを強制変更: %s → %s", genre2, candidate)
                break

    return config1, config2


def _process_record(rec: dict, rc, config: dict) -> None:
    """記事1本分の後処理（ログ・ジャンル履歴記録）"""
    status = rec.get("status", "")
    title = rec.get("title", "")
    if status == "published":
        rc.add_success(f"note投稿成功: {title}")
        genre_engine.record_article(data_dir=BUSINESS_DIR, title=title)
    elif status == "draft_ready":
        rc.add_failure(
            f"note投稿失敗→下書き保存: {title}",
            cause="note.com API エラーまたはCOOKIE未設定。ready/に保存済み。",
            needs_action=False,
        )
        genre_engine.record_article(data_dir=BUSINESS_DIR, title=title)


if __name__ == "__main__":
    main()
