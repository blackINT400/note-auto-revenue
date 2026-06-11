"""
pipeline.py: YouTube BGMパイプライン オーケストレーター

使い方:
  python pipeline.py                          # 本番実行
  python pipeline.py --dry-run               # ドライラン（アップロードスキップ）
  python pipeline.py --preview               # プレビュー（動画生成のみ、アップロードなし）
  python pipeline.py --duration short        # 60秒テスト
  python pipeline.py --duration medium       # 30分品質確認
  python pipeline.py --duration long         # 2時間本番
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
    "genre_focus": "smooth jazz, R&B, relaxing",
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


def _increment_video_count(data_dir: str) -> int:
    """video_count.jsonのカウンタをインクリメントして新しい値を返す"""
    count_path = Path(data_dir) / "video_count.json"
    count_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(count_path.read_text()).get("count", 0) if count_path.exists() else 0
    except Exception:
        current = 0
    new_count = current + 1
    count_path.write_text(json.dumps({"count": new_count}, indent=2))
    return new_count


def run_daily_pipeline(config: dict, dry_run: bool = False, preview: bool = False) -> dict:
    """デイリーパイプライン: リサーチ→作曲→組み立て→アップロード"""
    logger.info("=" * 50)
    logger.info("YouTube BGM デイリーパイプライン開始")
    logger.info(f"Dry-run: {dry_run} / Preview: {preview} / Duration: {config.get('duration_mode', 'short')}")
    logger.info("=" * 50)

    total_cost = 0.0
    results = {}

    sys.path.insert(0, str(Path(__file__).parent))

    # Step 1: トレンドリサーチ
    logger.info("[1/4] BGMトレンドリサーチ...")
    from agents.trend_researcher import research_bgm_trends
    research = research_bgm_trends(config, dry_run=dry_run)
    if not research.get("success"):
        logger.error(f"リサーチ失敗: {research.get('error')}")
        return {"success": False, "step": "research", **research}
    total_cost += research.get("cost_jpy", 0)
    results["research"] = research
    logger.info(f"  -> {len(research.get('concepts', []))}件のコンセプト取得 ({research['cost_jpy']:.1f}円)")

    # Step 2: 楽曲パッケージ生成
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

    # Step 4: アップロード or プレビュー保存
    video_file = package.get("video_file_path", "")
    if preview:
        logger.info("[4/4] プレビューモード: アップロードスキップ")
        preview_dir = Path("output/preview")
        preview_dir.mkdir(parents=True, exist_ok=True)
        upload = {"success": True, "preview": True, "video_id": None,
                  "message": "Preview mode: アップロードスキップ"}
        if video_file and Path(video_file).exists():
            import shutil
            dest = preview_dir / Path(video_file).name
            shutil.copy2(video_file, dest)
            upload["preview_path"] = str(dest)
            logger.info(f"  -> [PREVIEW] 動画を保存: {dest}")
            logger.info(f"  -> [PREVIEW] ファイルサイズ: {dest.stat().st_size / 1024 / 1024:.1f}MB")
            print(f"\n{'='*60}")
            print(f"  PREVIEW動画: {dest.resolve()}")
            print(f"{'='*60}\n")
        results["upload"] = upload
    else:
        logger.info("[4/4] YouTubeアップロード...")
        from agents.youtube_uploader import upload_video_metadata
        upload = upload_video_metadata(package, dry_run=dry_run)
        results["upload"] = upload
        if upload.get("dry_run"):
            logger.info(f"  -> [DRY-RUN] {upload['message']}")
        elif upload.get("success"):
            logger.info(f"  -> アップロード完了: video_id={upload.get('video_id')}")
            new_count = _increment_video_count(config.get("data_dir", "youtube_bgm/data"))
            logger.info(f"  -> video_count 更新: Vol.{new_count}")
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
    parser.add_argument("--preview", action="store_true", help="動画生成のみ、output/preview/に保存してアップロードしない")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--genre", default=None, help="ジャンルを上書き指定")
    parser.add_argument(
        "--duration",
        choices=["short", "medium", "long"],
        default="short",
        help="short=60秒 / medium=30分 / long=2時間",
    )
    args = parser.parse_args()

    config = load_config()
    if args.genre:
        config["genre_focus"] = args.genre
    config["duration_mode"] = args.duration
    env_channel = os.environ.get("CHANNEL_NAME", "")
    if env_channel:
        config["channel_name"] = env_channel

    if args.mode == "weekly":
        result = run_weekly_analysis(config)
    else:
        result = run_daily_pipeline(config, dry_run=args.dry_run, preview=args.preview)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
