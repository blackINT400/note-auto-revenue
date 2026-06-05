"""
main.py: YouTube副業収益チャンネル ビジネスランナー
daily: プロデューサーを起動して動画パッケージを生成
weekly: アナリストを起動してチャンネル分析を実行
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BUSINESS_DIR = Path(__file__).parent
CONFIG_PATH = BUSINESS_DIR / "config.yaml"


def _load_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["data_dir"] = str(BUSINESS_DIR)
    return cfg


def run_daily(config: dict) -> int:
    from agents.youtube.producer import produce_video_package
    result = produce_video_package(config)
    if result.get("success"):
        logger.info(
            f"動画パッケージ生成完了: {result.get('title', '')} "
            f"（品質: {result.get('quality_score', 0)}点 / コスト: {result.get('cost_jpy', 0):.1f}円）"
        )
        return 0
    else:
        logger.error(f"動画パッケージ生成失敗: {result.get('error', '')}")
        return 1


def run_weekly(config: dict) -> int:
    from agents.youtube.analyst import analyze_channel
    result = analyze_channel(config)
    if result.get("success"):
        logger.info(
            f"チャンネル分析完了: 健全度={result.get('channel_health', 'unknown')} "
            f"（コスト: {result.get('cost_jpy', 0):.1f}円）"
        )
        recommendations = result.get("recommendations", [])
        for rec in recommendations[:3]:
            logger.info(f"推奨[優先度{rec.get('priority', '?')}]: {rec.get('action', '')}")
        return 0
    else:
        logger.error(f"チャンネル分析失敗: {result.get('error', '')}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube副業収益チャンネル ビジネスランナー")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args()

    config = _load_config()
    logger.info(f"=== YouTube副業収益チャンネル [{args.mode}モード] 起動 ===")

    if args.mode == "daily":
        return run_daily(config)
    elif args.mode == "weekly":
        return run_weekly(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
