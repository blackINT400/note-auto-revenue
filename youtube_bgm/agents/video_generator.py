"""
video_generator.py: 背景動画 + アンビエント音楽を合成して動画ファイルを生成する

処理:
  1. Pixabay API で背景動画を検索・ダウンロード
  2. ffmpeg でロービート・アンビエント音楽を合成（複数sine波 + フィルタ）
  3. 背景動画（ループ）と音楽を結合して最終MP4を出力
"""
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

PIXABAY_API_URL = "https://pixabay.com/api/videos/"
DEFAULT_DURATION_SEC = 3600  # 1 hour


def _search_pixabay_video(keyword: str, api_key: str) -> str | None:
    """Pixabayで動画を検索してダウンロードURLを返す"""
    params = {
        "key": api_key,
        "q": keyword,
        "video_type": "film",
        "category": "nature",
        "min_width": 1280,
        "per_page": 5,
        "safesearch": "true",
    }
    try:
        r = requests.get(PIXABAY_API_URL, params=params, timeout=30)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            # fallback: より広いキーワードで再試行
            params["q"] = "nature relaxing"
            r = requests.get(PIXABAY_API_URL, params=params, timeout=30)
            r.raise_for_status()
            hits = r.json().get("hits", [])
        if hits:
            videos = hits[0].get("videos", {})
            # medium → large → small の優先度
            for size in ("medium", "large", "small"):
                url = videos.get(size, {}).get("url")
                if url:
                    return url
    except Exception as e:
        logger.warning(f"Pixabay検索エラー: {e}")
    return None


def _download_file(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"ダウンロードエラー: {e}")
        return False


def _generate_ambient_audio(output_path: Path, duration_sec: int, genre: str = "lofi") -> bool:
    """ffmpegでアンビエント音楽を合成する"""
    # ジャンル別の周波数設定
    presets = {
        "lofi": {
            "freqs": [130.8, 164.8, 196.0, 261.6, 329.6],  # C3 chord
            "amps":  [0.20,  0.15,  0.12,  0.10,  0.08],
            "lp_cutoff": 3500,
            "echo": "0.8:0.88:80:0.3",
        },
        "jazz": {
            "freqs": [146.8, 185.0, 220.0, 277.2, 370.0],  # Dm7
            "amps":  [0.18,  0.14,  0.14,  0.10,  0.08],
            "lp_cutoff": 5000,
            "echo": "0.7:0.80:60:0.25",
        },
        "ambient": {
            "freqs": [110.0, 138.6, 165.0, 220.0, 277.2],  # Am chord
            "amps":  [0.22,  0.12,  0.12,  0.10,  0.07],
            "lp_cutoff": 2500,
            "echo": "0.9:0.92:120:0.5",
        },
    }
    preset_key = "lofi"
    for k in presets:
        if k in genre.lower():
            preset_key = k
            break
    p = presets[preset_key]

    # aevalsrc で複数sine波を合成
    expr_parts = [f"sin({f}*2*PI*t)*{a}" for f, a in zip(p["freqs"], p["amps"])]
    # ゆっくりした音量モジュレーション（0.5Hz）を加えて「揺らぎ」を演出
    expr_parts.append("sin(0.5*2*PI*t)*0.04")
    expr = "+".join(expr_parts)

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:c=stereo",
        "-af", (
            f"lowpass=f={p['lp_cutoff']},"
            "highpass=f=60,"
            f"aecho={p['echo']},"
            "volume=0.85"
        ),
        "-t", str(duration_sec),
        "-acodec", "aac",
        "-b:a", "192k",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"音楽生成エラー: {result.stderr[-500:]}")
        return False
    logger.info(f"アンビエント音楽生成完了: {output_path} ({duration_sec}秒)")
    return True


def _combine_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    duration_sec: int,
) -> bool:
    """背景動画をループして音楽と合成する"""
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",      # 動画を無限ループ
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:a", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-t", str(duration_sec),
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        logger.error(f"動画合成エラー: {result.stderr[-500:]}")
        return False
    logger.info(f"動画合成完了: {output_path}")
    return True


def generate_bgm_video(
    concept: dict,
    output_dir: Path,
    duration_sec: int = DEFAULT_DURATION_SEC,
) -> str | None:
    """
    BGM動画を生成してファイルパスを返す。
    失敗した場合は None を返す。
    """
    genre = concept.get("genre", "lofi")
    title_slug = concept.get("title", "bgm")[:30].replace(" ", "_").replace("/", "-")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Cap at 1 hour to stay within CI time limits
    duration_sec = min(duration_sec, 3600)
    output_path = output_dir / f"{title_slug}.mp4"

    if output_path.exists():
        logger.info(f"既存動画を再利用: {output_path}")
        return str(output_path)

    pixabay_key = os.environ.get("PIXABAY_API_KEY")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audio_path = tmp / "music.aac"
        bg_video_path = tmp / "background.mp4"

        # Step 1: 音楽生成
        logger.info("アンビエント音楽を生成中...")
        if not _generate_ambient_audio(audio_path, duration_sec, genre):
            logger.error("音楽生成失敗")
            return None

        # Step 2: 背景動画取得
        bg_ok = False
        if pixabay_key:
            search_keyword = concept.get("mood", "nature relaxing calm")
            logger.info(f"Pixabay背景動画を検索: {search_keyword}")
            video_url = _search_pixabay_video(search_keyword, pixabay_key)
            if video_url:
                bg_ok = _download_file(video_url, bg_video_path)

        if not bg_ok:
            # フォールバック: ffmpegでグラデーション背景を生成
            logger.info("フォールバック: グラデーション背景を生成")
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "gradients=s=1280x720:seed=42:speed=0.002:type=radial",
                "-t", "30",
                "-c:v", "libx264", "-preset", "fast",
                str(bg_video_path),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                # 最終フォールバック: 単色背景
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", "color=c=0x1a1a2e:size=1280x720:rate=1",
                    "-t", "10",
                    "-c:v", "libx264",
                    str(bg_video_path),
                ]
                subprocess.run(cmd, capture_output=True)
            bg_ok = bg_video_path.exists()

        if not bg_ok:
            logger.error("背景動画の準備失敗")
            return None

        # Step 3: 合成
        logger.info("動画を合成中...")
        if not _combine_video_audio(bg_video_path, audio_path, output_path, duration_sec):
            return None

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(f"BGM動画生成完了: {output_path} ({size_mb:.1f} MB)")
    return str(output_path)
