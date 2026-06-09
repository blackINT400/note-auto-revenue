"""
pipeline.py: YouTube BGMパイプライン オーケストレーター

使い方:
  python pipeline.py               # 本番実行
  python pipeline.py --dry-run     # ドライラン（アップロードスキップ）
  python pipeline.py --mode weekly # 週次分析モード
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("youtube_bgm.pipeline")

JST = timezone(timedelta(hours=9))

DEFAULT_CONFIG = {
    "channel_name": "Relaxing BGM Japan",
    "genre_focus": "lo-fi, study music, relaxing, sleep music",
    "target_use": "作業用・勉強用・睡眠用・カフェBGM",
    "data_dir": "youtube_bgm/data",
    "monthly_cost_limit": 1000,
    "dry_run": False,
}


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        import yaml
        with open(config_path) as f:
            return {**DEFAULT_CONFIG, **yaml.safe_load(f)}
    return DEFAULT_CONFIG


def run_daily_pipeline(config: dict, dry_run: bool = False) -> dict:
    """デイリーパイプライン: リサーチ→作曲→組み立て→アップロード"""
    logger.info("=" * 50)
    logger.info("YouTube BGM デイリーパイプライン開始")
    logger.info(f"Dry-run: {dry_run}")
    logger.info("=" * 50)

    total_cost = 0.0
    results = {}

    # Step 1: トレンドリサーチ
    logger.info("[1/4] BGMトレンドリサーチ...")
    sys.path.insert(0, str(Path(__file__).parent))
    from agents.trend_researcher import research_bgm_trends
    research = research_bgm_trends(config, dry_run=dry_run)
    if not research.get("success"):
        logger.error(f"リサーチ失敗: {research.get('error')}")
        return {"success": False, "step": "research", **research}
    total_cost += research.get("cost_jpy", 0)
    results["research"] = research
    logger.info(f"  -> {len(research.get('concepts', []))}件のコンセプト取得 ({research['cost_jpy']:.1f}円)")

    # Step 2: 推奨コンセプトで楽曲パッケージ生成
    logger.info("[2/4] 楽曲パッケージ生成...")
    from agents.music_composer import compose_music_package
    idx = research.get("recommended_index", 0)
    concept = research["concepts"][idx]
    music = compose_music_package(concept, dry_run=dry_run)
    if not music.get("success"):
        logger.error(f"楽曲生成失敗: {music.get('error')}")
        return {"success": False, "step": "music", **music}
    total_cost += music.get("cost_jpy", 0)
    results["music"] = music
    logger.info(f"  -> 楽曲パッケージ生成完了 ({music['cost_jpy']:.1f}円)")

    # Step 3: 動画パッケージ組み立て
    logger.info("[3/4] 動画パッケージ組み立て...")
    from agents.video_assembler import assemble_video_package
    package = assemble_video_package(music, config)
    results["package"] = package
    logger.info(f"  -> {package['title']}")

    # Step 4: アップロード
    logger.info("[4/4] YouTubeアップロード...")
    from agents.youtube_uploader import upload_video_metadata
    upload = upload_video_metadata(package, dry_run=dry_run)
    results["upload"] = upload
    if upload.get("dry_run"):
        logger.info(f"  -> [DRY-RUN] {upload['message']}")
    elif upload.get("success"):
        logger.info(f"  -> アップロード完了: video_id={upload.get('video_id')}")
    else:
        logger.error(f"  -> アップロード失敗: {upload.get('error')}")

    logger.info("=" * 50)
    logger.info(f"パイプライン完了 | 総コスト: {total_cost:.1f}円")
    logger.info("=" * 50)

    return {
        "success": True,
        "total_cost_jpy": round(total_cost, 2),
        "results": results,
        "executed_at": datetime.now(JST).isoformat(),
    }


def run_weekly_analysis(config: dict) -> dict:
    """週次分析モード: KPIサマリーをログ出力"""
    logger.info("週次分析モード")
    data_dir = Path(config.get("data_dir", "youtube_bgm/data"))
    ready_dir = data_dir / "ready"
    packages = list(ready_dir.glob("*.json")) if ready_dir.exists() else []
    logger.info(f"今週生成されたパッケージ: {len(packages)}件")
    for p in packages[-7:]:
        logger.info(f"  {p.name}")
    return {"success": True, "packages_count": len(packages)}


def main():
    parser = argparse.ArgumentParser(description="YouTube BGM Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="アップロードをスキップ")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--genre", default=None, help="ジャンルを上書き指定")
    args = parser.parse_args()

    config = load_config()
    if args.genre:
        config["genre_focus"] = args.genre

    if args.mode == "weekly":
        result = run_weekly_analysis(config)
    else:
        result = run_daily_pipeline(config, dry_run=args.dry_run)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
