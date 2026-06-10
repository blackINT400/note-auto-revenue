"""
video_assembler.py: 動画パッケージ組み立てエージェント
楽曲パッケージから動画メタデータ・アップロード用パッケージを生成する
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from agents.video_generator import generate_bgm_video

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def assemble_video_package(music_package: dict, config: dict) -> dict:
    """楽曲パッケージから動画アップロードパッケージを組み立てる"""
    concept = music_package.get("concept", {})
    channel_name = config.get("channel_name", "BGM Channel")

    title = concept.get("title", "Relaxing BGM")
    tags = music_package.get("tags", []) + concept.get("tags", [])
    tags = list(dict.fromkeys(tags))[:15]  # dedup, max 15

    description = music_package.get("description_jp", "")
    description += f"\n\n▶ チャンネル登録: {channel_name}\n"
    description += "\n🎵 This music is free to use with credit."

    # Optimal upload time: weekday 18:00 JST
    now = datetime.now(JST)
    days_to_weekday = (0 - now.weekday()) % 7 or 7
    upload_dt = (now + timedelta(days=days_to_weekday)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )

    genre = concept.get("genre", "lofi")
    mood = concept.get("mood", "relaxing")
    visual_concept = music_package.get("visual_concept", "") or f"{genre} scene: {mood}"

    package = {
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": "10",  # Music
        "privacy_status": "public",
        "scheduled_publish_at": upload_dt.isoformat(),
        "thumbnail_text": music_package.get("thumbnail_text", title),
        "visual_concept": visual_concept,
        "suno_prompt": music_package.get("suno_prompt", ""),
        "udio_prompt": music_package.get("udio_prompt", ""),
        "music_structure": music_package.get("structure", {}),
        "assembled_at": datetime.now(JST).isoformat(),
        "cost_jpy": music_package.get("cost_jpy", 0),
        "success": True,
    }

    # 動画ファイル生成
    duration_mode = config.get("duration_mode", "short")
    if duration_mode == "short":
        duration_sec = 60
    else:
        duration_min = concept.get("duration_minutes", 60)
        duration_sec = min(duration_min * 60, 3600)
    video_output_dir = Path(config.get("data_dir", "youtube_bgm/data")) / "videos"

    video_path = generate_bgm_video(concept, video_output_dir, duration_sec, duration_mode=duration_mode)
    if video_path:
        package["video_file_path"] = video_path
        logger.info(f"動画ファイル生成完了: {video_path}")
    else:
        logger.warning("動画ファイル生成失敗 - フォールバック動画を使用")

    # Save package
    output_dir = Path(config.get("data_dir", "youtube_bgm/data")) / "ready"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(JST).strftime("%Y%m%d")
    out_path = output_dir / f"{date_str}_video_package.json"
    out_path.write_text(json.dumps(package, ensure_ascii=False, indent=2))

    logger.info(f"動画パッケージ組み立て完了: {title} -> {out_path}")
    return package
