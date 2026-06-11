"""
video_generator.py: Pixabayループ動画 + 音楽合成

処理:
  1. Pixabay Video APIでループ動画素材を取得（fireplace/rain/ocean/forest）
  2. stream_loop -1 で音楽と同じ長さにループ
  3. 最初・最後3秒のフェードのみ
  4. 取得失敗時はPollinations静止画・完全固定フォールバック
"""
import logging
import os
import random
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DEFAULT_DURATION_SEC = 3600
FPS = 24
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"

NEGATIVE_PROMPT = "text, watermark, people, faces, logos, words, signature, ugly, blurry"

# Pixabay検索キーワードと対応シーン
PIXABAY_KEYWORDS = [
    "fireplace",
    "rain window",
    "ocean waves",
    "forest nature",
]

# Pollinations静止画フォールバック用シーン
FALLBACK_SCENES = [
    (
        "morning_forest",
        "luxury modern living room, floor-to-ceiling windows, "
        "japanese forest and lake view outside, morning sunlight, "
        "Eames lounge chair, indoor plants, 4K cinematic, no people",
    ),
    (
        "evening_ocean",
        "ultra-luxury cliff villa, panoramic floor-to-ceiling windows, "
        "ocean sunset view, hanging fireplace with flames, "
        "golden hour reflections on water, cinematic, no people",
    ),
    (
        "night_mountain",
        "cozy luxury chalet, large windows, "
        "snowy mountain forest at night, warm fireplace glow inside, "
        "cinematic depth of field, no people",
    ),
    (
        "rainy_cafe",
        "luxury indoor jazz cafe by the window, "
        "rain drops on large glass windows, green forest outside, "
        "coffee cups, bookshelf, warm lamp light, 4K, no people",
    ),
]


# ─── Pixabay動画取得 ───────────────────────────────────────────────────

def _search_pixabay_video(keyword: str, api_key: str) -> str | None:
    """Pixabayでキーワード検索し、mp4 URLを返す"""
    params = {
        "key": api_key,
        "q": keyword,
        "video_type": "film",
        "min_width": 1280,
        "per_page": 10,
        "safesearch": "true",
    }
    try:
        r = requests.get(PIXABAY_API_URL, params=params, timeout=30)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        # duration 10〞60秒のものを優先
        filtered = [h for h in hits if 10 <= h.get("duration", 0) <= 60]
        if not filtered:
            filtered = hits
        if not filtered:
            return None
        hit = random.choice(filtered[:5])
        videos = hit.get("videos", {})
        for size in ("medium", "large", "small", "tiny"):
            url = videos.get(size, {}).get("url")
            if url:
                logger.info(f"Pixabay動画発見: '{keyword}' -> {size} ({hit.get('duration')}s)")
                return url
    except Exception as e:
        logger.warning(f"Pixabay検索エラー '{keyword}': {e}")
    return None


def _download_file(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        size_mb = dest.stat().st_size / 1024 / 1024
        logger.info(f"ダウンロード完了: {dest.name} ({size_mb:.1f}MB)")
        return True
    except Exception as e:
        logger.error(f"ダウンロードエラー: {e}")
        return False


def _get_pixabay_video(tmp: Path, genre: str) -> Path | None:
    """Pixabayからジャンルに合ったループ動画を取得"""
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        logger.warning("PIXABAY_API_KEY未設定")
        return None

    # ジャンルに応じたキーワード順序
    genre_lower = genre.lower()
    if "jazz" in genre_lower or "lounge" in genre_lower or "cafe" in genre_lower:
        keywords = ["fireplace", "rain window", "forest nature", "ocean waves"]
    elif "rain" in genre_lower or "chill" in genre_lower:
        keywords = ["rain window", "forest nature", "fireplace", "ocean waves"]
    elif "ocean" in genre_lower or "wave" in genre_lower or "sea" in genre_lower:
        keywords = ["ocean waves", "rain window", "fireplace", "forest nature"]
    else:
        keywords = random.sample(PIXABAY_KEYWORDS, len(PIXABAY_KEYWORDS))

    for keyword in keywords:
        # キャッシュ確認
        cache_key = keyword.replace(" ", "_")
        cached = tmp / f"pixabay_{cache_key}.mp4"
        if cached.exists():
            return cached

        url = _search_pixabay_video(keyword, api_key)
        if url and _download_file(url, cached):
            return cached

    return None


# ─── Pollinations静止画フォールバック ─────────────────────────────────

def _download_pollinations_image(prompt: str, seed: int, dest: Path) -> bool:
    url = (
        f"{POLLINATIONS_BASE}/{urllib.parse.quote(prompt)}"
        f"?width=1280&height=720&seed={seed}&nologo=true"
        f"&negative={urllib.parse.quote(NEGATIVE_PROMPT)}"
    )
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        if len(r.content) < 5000:
            return False
        dest.write_bytes(r.content)
        logger.info(f"Pollinations画像取得完了: {dest.name}")
        return True
    except Exception as e:
        logger.warning(f"Pollinationsエラー: {e}")
        return False


def _image_to_static_video(image_path: Path, output_path: Path, duration: int) -> bool:
    """静止画像を固定表示動画に変換（動きなし）"""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", "1",
        "-i", str(image_path),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"静止画像変換エラー: {result.stderr[-300:]}")
        return False
    return True


# ─── 音楽生成 ───────────────────────────────────────────────────

def _generate_ambient_audio(output_path: Path, duration_sec: int, genre: str = "lofi") -> bool:
    presets = {
        "lofi": {
            "freqs": [130.8, 164.8, 196.0, 261.6, 329.6],
            "amps":  [0.20,  0.15,  0.12,  0.10,  0.08],
            "lp_cutoff": 3500,
            "echo": "0.8:0.88:80:0.3",
        },
        "jazz": {
            "freqs": [146.8, 185.0, 220.0, 277.2, 370.0],
            "amps":  [0.18,  0.14,  0.14,  0.10,  0.08],
            "lp_cutoff": 5000,
            "echo": "0.7:0.80:60:0.25",
        },
        "ambient": {
            "freqs": [110.0, 138.6, 165.0, 220.0, 277.2],
            "amps":  [0.22,  0.12,  0.12,  0.10,  0.07],
            "lp_cutoff": 2500,
            "echo": "0.9:0.92:120:0.5",
        },
        "piano": {
            "freqs": [261.6, 329.6, 392.0, 523.2, 659.3],
            "amps":  [0.18,  0.14,  0.12,  0.08,  0.06],
            "lp_cutoff": 8000,
            "echo": "0.6:0.75:50:0.2",
        },
    }
    preset_key = "lofi"
    for k in presets:
        if k in genre.lower():
            preset_key = k
            break
    p = presets[preset_key]

    expr_parts = [f"sin({f}*2*PI*t)*{a}" for f, a in zip(p["freqs"], p["amps"])]
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
    logger.info(f"音楽生成完了: {output_path.name} ({duration_sec}s)")
    return True


# ─── 最終合成 ───────────────────────────────────────────────────

def _combine_loop_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    duration_sec: int,
    fade_sec: int = 3,
) -> bool:
    """
    動画をstream_loopで音楽長にループし、
    最初・最後{fade_sec}秒のフェードを適用。
    """
    # ビデオフィルタ: スケール + フェードイン/アウト
    vf = (
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"fade=t=in:st=0:d={fade_sec},"
        f"fade=t=out:st={duration_sec - fade_sec}:d={fade_sec}"
    )
    # オーディオフィルタ: フェードイン/アウト
    af = (
        f"afade=t=in:st=0:d={fade_sec},"
        f"afade=t=out:st={duration_sec - fade_sec}:d={fade_sec}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
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
    logger.info(f"動画合成完了: {output_path.name}")
    return True


# ─── メインエントリ ───────────────────────────────────────────────

def generate_bgm_video(
    concept: dict,
    output_dir: Path,
    duration_sec: int = DEFAULT_DURATION_SEC,
    duration_mode: str = "short",
    channel_name: str = "",
) -> str | None:
    genre = concept.get("genre", "lofi")
    title_slug = concept.get("title", "bgm")[:40].replace(" ", "_").replace("/", "-")
    output_dir.mkdir(parents=True, exist_ok=True)
    duration_sec = min(duration_sec, 7200)
    output_path = output_dir / f"{title_slug}.mp4"

    if output_path.exists():
        logger.info(f"既存動画を再利用: {output_path}")
        return str(output_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audio_path = tmp / "music.aac"
        bg_video: Path | None = None

        # Step 1: 音楽生成
        logger.info(f"音楽生成中... ({duration_sec}s, genre={genre})")
        if not _generate_ambient_audio(audio_path, duration_sec, genre):
            logger.error("音楽生成失敗")
            return None

        # Step 2: Pixabayループ動画を取得
        logger.info(f"Pixabayループ動画を取得中... (genre={genre})")
        bg_video = _get_pixabay_video(tmp, genre)

        if bg_video:
            logger.info(f"Pixabay動画取得成功: {bg_video.name}")
        else:
            # Step 3a: Pollinations静止画フォールバック
            logger.info("Pixabay失敗 -> Pollinations静止画フォールバック")
            scene_name, scene_prompt = random.choice(FALLBACK_SCENES)
            seed = random.randint(1000, 9999)
            img_path = tmp / f"{scene_name}.jpg"
            static_video = tmp / f"{scene_name}.mp4"
            # 静止画像取得は10秒分だけ作ればよい（ループする）
            clip_dur = min(10, duration_sec)
            if _download_pollinations_image(scene_prompt, seed, img_path):
                if _image_to_static_video(img_path, static_video, clip_dur):
                    bg_video = static_video
                    logger.info(f"Pollinations静止画使用: {scene_name}")

        if bg_video is None:
            # Step 3b: 単色背景最終フォールバック
            logger.info("単色背景最終フォールバック")
            fallback_path = tmp / "fallback.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=0x1a1a2e:size=1280x720:rate=1",
                "-t", "5", "-c:v", "libx264", str(fallback_path),
            ], capture_output=True)
            if fallback_path.exists():
                bg_video = fallback_path

        if bg_video is None:
            logger.error("背景動画の準備に全て失敗")
            return None

        # Step 4: ループ+音楽合成
        logger.info(f"最終合成中: {bg_video.name} -> {duration_sec}s")
        if not _combine_loop_video_audio(bg_video, audio_path, output_path, duration_sec):
            return None

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(f"BGM動画生成完了: {output_path.name} ({size_mb:.1f}MB)")
    return str(output_path)
