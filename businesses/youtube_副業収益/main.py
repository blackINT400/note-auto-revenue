import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
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
        logger.info(f"動画パッケージ完成: {result.get('title', '')} (品質: {result.get('quality_score', 0)}点 / コスト: {result.get('cost_jpy', 0):.1f}円)")
        return 0
    logger.error(f"失敗: {result.get('error', '')}")
    return 1


def run_weekly(config: dict) -> int:
    from agents.youtube.analyst import analyze_channel
    result = analyze_channel(config)
    if result.get("success"):
        logger.info(f"分析完了: 健全度={result.get('channel_health', '')} (コスト: {result.get('cost_jpy', 0):.1f}円)")
        for rec in result.get("recommendations", [])[:3]:
            logger.info(f"推奨[優先度{rec.get('priority', '')}]: {rec.get('action', '')}")
        return 0
    logger.error(f"失敗: {result.get('error', '')}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args()
    config = _load_config()
    logger.info(f"=== YouTube副業収益 [{args.mode}モード] ===")
    return run_daily(config) if args.mode == "daily" else run_weekly(config)


if __name__ == "__main__":
    sys.exit(main())
