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

import json

import yaml

from empire.playbooks.note_magazine import setup, run, report

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

    # ── アフィリエイト案件ジャンルを読み込む ────────────────────────────
    affiliate_genres: set[str] = set()
    if AFFILIATES_PATH.exists():
        try:
            af_data = yaml.safe_load(AFFILIATES_PATH.read_text(encoding="utf-8"))
            for af in (af_data or {}).get("affiliates", []):
                for g in af.get("genres", []):
                    affiliate_genres.add(g)
        except Exception:
            pass

    # ── 市場パターンで「今読まれているジャンル」を読み込む ──────────────
    hot_genres: set[str] = set()
    if PATTERNS_PATH.exists():
        try:
            pt = json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
            for rs in pt.get("latest", {}).get("resonance_structures", []):
                hot_genres.add(rs.get("genre", ""))
        except Exception:
            pass

    # ── ジャンルローテーション（アフィリエイト×市場パターン考慮）────────
    genres = cfg.get("genre_rotation", [])
    idx = cfg.get("auto_strategy", {}).get("current_genre_index", 0)
    if genres:
        selected_genre, next_idx = _select_genre(
            genres, idx, affiliate_genres, hot_genres
        )
        cfg["today_genre"] = selected_genre
        cfg.setdefault("auto_strategy", {})["current_genre_index"] = next_idx
        CONFIG_PATH.write_text(
            yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        reason = _genre_select_reason(selected_genre, affiliate_genres, hot_genres)
        logger.info("ジャンル選定: %s （%s）", selected_genre, reason)

    return cfg


def _genre_matches(genre: str, keyword_set: set[str]) -> bool:
    """ジャンル文字列がキーワードセットのいずれかを含むか判定"""
    return any(kw in genre for kw in keyword_set)


def _select_genre(
    genres: list[str],
    idx: int,
    affiliate_genres: set[str],
    hot_genres: set[str],
) -> tuple[str, int]:
    """
    優先順位:
      1. アフィリエイト案件 × 市場パターン 両方一致
      2. アフィリエイト案件 一致
      3. 市場パターン 一致
      4. 通常ローテーション（フォールバック）
    ※ 先読みは最大3スロットまで。飛ばした場合はそのindexから継続。
    """
    n = len(genres)
    look_ahead = min(3, n)

    # 各スコアを計算
    scores: list[tuple[int, int, str]] = []  # (score, offset, genre)
    for offset in range(look_ahead):
        g = genres[(idx + offset) % n]
        score = 0
        if _genre_matches(g, affiliate_genres):
            score += 2
        if _genre_matches(g, hot_genres):
            score += 1
        scores.append((score, offset, g))

    # スコア降順、offset昇順でソート（同スコアなら近い方を優先）
    scores.sort(key=lambda x: (-x[0], x[1]))
    best_score, best_offset, best_genre = scores[0]

    next_idx = (idx + best_offset + 1) % n
    return best_genre, next_idx


def _genre_select_reason(genre: str, affiliate_genres: set[str], hot_genres: set[str]) -> str:
    reasons = []
    if _genre_matches(genre, affiliate_genres):
        reasons.append("アフィリエイト案件あり")
    if _genre_matches(genre, hot_genres):
        reasons.append("市場パターン一致")
    return "、".join(reasons) if reasons else "通常ローテーション"


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
