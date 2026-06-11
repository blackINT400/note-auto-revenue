"""
video_generator.py: Pexelsループ動画 + 音楽合成

処理:
  1. Pexels Video APIでmood別動画素材を取得（landscape/HD）
  2. stream_loop -1 で音楽と同じ長さにループ
  3. フェードイン・フェードアウト3秒のみ
  4. 取得失敗時: Pollinations静止画（固定・動きなし）
  5. 最終フォールバック: 単色背景
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
PEXELS_API_URL = "https://api.pexels.com/videos/search"
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
NEGATIVE_PROMPT = "text, watermark, people, faces, logos, words, signature, ugly, blurry"

MOOD_KEYWORDS: dict[str, list[str]] = {
    "lofi":      ["cozy living room interior", "luxury apartment interior", "cafe interior window"],
    "jazz":      ["luxury hotel lobby interior", "penthouse living room", "lounge bar interior"],
    "ambient":   ["luxury villa ocean terrace", "cabin interior forest window", "lake house interior"],
    "cyberpunk": ["city night rooftop view", "penthouse city night"],
    "piano":     ["luxury room interior cozy", "elegant living room"],
    "focus":     ["home office interior window", "library interior cozy"],
    "r&b":       ["cozy living room interior", "luxury apartment interior", "lounge bar interior"],
    "smooth":    ["luxury hotel lobby interior", "penthouse living room", "cozy living room interior"],
    "sleep":     ["luxury villa ocean terrace", "cabin interior forest window", "cozy home interior"],
    "relax":     ["luxury villa ocean terrace", "lake house interior", "cabin interior forest window"],
}
DEFAULT_KEYWORDS = ["cozy living room interior", "luxury apartment interior", "luxury villa ocean terrace", "cabin interior forest window"]

FALLBACK_SCENES = [
    ("morning_forest", "luxury modern living room, floor-to-ceiling windows, japanese forest and lake view outside, morning sunlight, Eames lounge chair, indoor plants, 4K cinematic, no people"),
    ("evening_ocean",  "ultra-luxury cliff villa, panoramic floor-to-ceiling windows, ocean sunset view, hanging fireplace with flames, golden hour reflections on water, cinematic, no people"),
    ("night_mountain", "cozy luxury chalet, large windows, snowy mountain forest at night, warm fireplace glow inside, cinematic depth of field, no people"),
    ("rainy_cafe",     "luxury indoor jazz cafe by the window, rain drops on large glass windows, green forest outside, coffee cups, bookshelf, warm lamp light, 4K, no people"),
]


def _keywords_for_genre(genre: str) -> list[str]:
    genre_lower = genre.lower()
    for key, kws in MOOD_KEYWORDS.items():
        if key in genre_lower:
            return kws
    return DEFAULT_KEYWORDS


def _search_pexels_video(keyword: str, api_key: str) -> str | None:
    headers = {"Authorization": api_key}
    params = {"query": keyword, "per_page": 15, "orientation": "landscape"}
    try:
        r = requests.get(PEXELS_API_URL, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            return None
        video = random.choice(videos[:8])
        files = video.get("video_files", [])
        mp4_hd = [f for f in files if f.get("width", 0) >= 1280 and "mp4" in f.get("file_type", "")]
        candidates = mp4_hd if mp4_hd else [f for f in files if f.get("width", 0) >= 1280]
        if not candidates:
            candidates = files
        if not candidates:
            return None
        best = max(candidates, key=lambda f: f.get("width", 0))
        url = best.get("link")
        if url:
            logger.info(f"Pexels動画発見: '{keyword}' -> {best.get('width')}p ({video.get('duration', 0)}s)")
            return url
    except Exception as e:
        logger.warning(f"Pexels検索エラー '{keyword}': {e}")
    return None


def _download_file(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        logger.info(f"ダウンロード完了: {dest.name} ({dest.stat().st_size/1024/1024:.1f}MB)")
        return True
    except Exception as e:
        logger.error(f"ダウンロードエラー: {e}")
        return False


def _get_pexels_video(assets_dir: Path, genre: str) -> Path | None:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        logger.warning("PEXELS_API_KEY未設定 -> スキップ")
        return None
    keywords = _keywords_for_genre(genre)
    mood_slug = genre.lower().split()[0][:10]
    cache_dir = assets_dir / mood_slug
    cache_dir.mkdir(parents=True, exist_ok=True)
    for keyword in keywords:
        cache_key = keyword.replace(" ", "_")
        cached = cache_dir / f"{cache_key}.mp4"
        if cached.exists() and cached.stat().st_size > 100_000:
            logger.info(f"キャッシュヒット: {cached.name}")
            return cached
        url = _search_pexels_video(keyword, api_key)
        if url and _download_file(url, cached):
            return cached
    return None


def _download_pollinations_image(prompt: str, seed: int, dest: Path) -> bool:
    url = (f"{POLLINATIONS_BASE}/{urllib.parse.quote(prompt)}"
           f"?width=1280&height=720&seed={seed}&nologo=true"
           f"&negative={urllib.parse.quote(NEGATIVE_PROMPT)}")
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        if len(r.content) < 5000:
            return False
        dest.write_bytes(r.content)
        logger.info(f"Pollinations画像取得: {dest.name}")
        return True
    except Exception as e:
        logger.warning(f"Pollinationsエラー: {e}")
        return False


def _image_to_static_video(image_path: Path, output_path: Path, duration: int) -> bool:
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-framerate", "1", "-i", str(image_path),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
        "-t", str(duration), "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode == 0


def _generate_ambient_audio(output_path: Path, duration_sec: int, genre: str = "lofi") -> bool:
    presets = {
        "lofi":    {"freqs": [130.8, 164.8, 196.0, 261.6, 329.6], "amps": [0.20, 0.15, 0.12, 0.10, 0.08], "lp": 3500, "echo": "0.8:0.88:80:0.3"},
        "jazz":    {"freqs": [146.8, 185.0, 220.0, 277.2, 370.0], "amps": [0.18, 0.14, 0.14, 0.10, 0.08], "lp": 5000, "echo": "0.7:0.80:60:0.25"},
        "ambient": {"freqs": [110.0, 138.6, 165.0, 220.0, 277.2], "amps": [0.22, 0.12, 0.12, 0.10, 0.07], "lp": 2500, "echo": "0.9:0.92:120:0.5"},
        "piano":   {"freqs": [261.6, 329.6, 392.0, 523.2, 659.3], "amps": [0.18, 0.14, 0.12, 0.08, 0.06], "lp": 8000, "echo": "0.6:0.75:50:0.2"},
    }
    genre_lower = genre.lower()
    preset_key = next((k for k in presets if k in genre_lower), "lofi")
    p = presets[preset_key]
    expr = "+".join(f"sin({f}*2*PI*t)*{a}" for f, a in zip(p["freqs"], p["amps"]))
    expr += "+sin(0.5*2*PI*t)*0.04"
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:c=stereo",
        "-af", f"lowpass=f={p['lp']},highpass=f=60,aecho={p['echo']},volume=0.85",
        "-t", str(duration_sec), "-acodec", "aac", "-b:a", "192k", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"音楽生成エラー: {result.stderr[-500:]}")
        return False
    logger.info(f"音楽生成完了: {output_path.name} ({duration_sec}s)")
    return True


def _combine_loop_video_audio(
    video_path: Path, audio_path: Path, output_path: Path,
    duration_sec: int, fade_sec: int = 3,
) -> bool:
    vf = (
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"fade=t=in:st=0:d={fade_sec},"
        f"fade=t=out:st={duration_sec - fade_sec}:d={fade_sec}"
    )
    af = f"afade=t=in:st=0:d={fade_sec},afade=t=out:st={duration_sec - fade_sec}:d={fade_sec}"
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(video_path),
        "-i", str(audio_path),
        "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(duration_sec), "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        logger.error(f"動画合成エラー: {result.stderr[-500:]}")
        return False
    logger.info(f"動画合成完了: {output_path.name}")
    return True


def generate_bgm_video(
    concept: dict,
    output_dir: Path,
    duration_sec: int = DEFAULT_DURATION_SEC,
    duration_mode: str = "short",
    channel_name: str = "",
    assets_dir: Path | None = None,
) -> str | None:
    genre = concept.get("genre", "lofi")
    title_slug = concept.get("title", "bgm")[:40].replace(" ", "_").replace("/", "-")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    duration_sec = min(duration_sec, 7200)
    output_path = output_dir / f"{title_slug}.mp4"

    if output_path.exists():
        logger.info(f"既存動画を再利用: {output_path}")
        return str(output_path)

    if assets_dir is None:
        assets_dir = output_dir.parent.parent / "assets" / "video"
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audio_path = tmp / "music.aac"
        bg_video: Path | None = None

        logger.info(f"音楽生成中... ({duration_sec}s, genre={genre})")
        if not _generate_ambient_audio(audio_path, duration_sec, genre):
            return None

        logger.info(f"Pexels動画取得中... (genre={genre})")
        bg_video = _get_pexels_video(assets_dir, genre)

        if not bg_video:
            logger.info("Pexels失敗 -> Pollinations静止画フォールバック")
            scene_name, scene_prompt = random.choice(FALLBACK_SCENES)
            seed = random.randint(1000, 9999)
            img_path = tmp / f"{scene_name}.jpg"
            static_video = tmp / f"{scene_name}.mp4"
            clip_dur = min(10, duration_sec)
            if _download_pollinations_image(scene_prompt, seed, img_path):
                if _image_to_static_video(img_path, static_video, clip_dur):
                    bg_video = static_video
                    logger.info(f"Pollinations静止画使用: {scene_name}")

        if not bg_video:
            logger.info("単色背景最終フォールバック")
            fallback_path = tmp / "fallback.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=0x1a1a2e:size=1280x720:rate=1",
                "-t", "5", "-c:v", "libx264", str(fallback_path),
            ], capture_output=True)
            if fallback_path.exists():
                bg_video = fallback_path

        if not bg_video:
            logger.error("背景動画の準備に全て失敗")
            return None

        logger.info(f"最終合成中: {Path(bg_video).name} -> {duration_sec}s")
        if not _combine_loop_video_audio(bg_video, audio_path, output_path, duration_sec):
            return None

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(f"BGM動画生成完了: {output_path.name} ({size_mb:.1f}MB)")
    return str(output_path)
