"""
video_generator.py: 映像ループ品質大幅改善版

処理:
  1. Pollinations.AI でジャンル別シーン画像を複数枚生成
     （始点と終点を同一seedにしてシームレスループを設計）
  2. 各画像に zoompan（ゆっくりズームイン/アウト）を適用してクリップ化
  3. xfade フィルターで3秒クロスフェード結合（dissolve/fadeblack をランダム選択）
  4. 生成したサイクル動画を stream_loop でループして1時間動画に仕上げ
  5. アンビエント音楽（ffmpeg sine波合成）と合成
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
FADE_DUR = 3  # crossfade seconds
FPS = 24
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

QUALITY_SUFFIX = (
    "4K cinematic, shallow depth of field, golden hour lighting, "
    "luxury hotel atmosphere, seamless loop ready, photorealistic, "
    "high detail, no motion blur, dramatic lighting"
)
NEGATIVE_PROMPT = "text, watermark, people, faces, hands, logos, words, signature, ugly, blurry"

# mood/genre 別シーンキーワード
SCENE_CATEGORIES: dict[str, list[str]] = {
    "lofi": [
        "luxury hotel fireplace warm glow cozy interior",
        "rain drops streaming down window glass night city bokeh",
        "cozy cafe candle bokeh warm amber light",
        "vintage record player turntable warm wood bokeh",
        "warm reading lamp antique books library shelves",
        "steaming coffee cup window rain blur",
    ],
    "jazz": [
        "luxury hotel bar fireplace warm amber glow",
        "jazz club neon sign rain reflection puddle",
        "elegant candlelit dinner table wine glass bokeh",
        "vintage vinyl record spinning warm light",
        "rainy window city lights bokeh night blue",
        "saxophone silhouette stage spotlight smoke",
    ],
    "ambient": [
        "ocean waves shoreline long exposure misty sunrise",
        "mountain lake surface ripples morning mist fog",
        "starry night sky milky way mountain reflection",
        "forest waterfall long exposure silky water",
        "northern lights aurora borealis reflection lake",
        "desert sand dunes golden hour dramatic shadows",
    ],
    "cyberpunk": [
        "wet city street neon sign reflection puddle night",
        "rainy window neon lights blur bokeh cyberpunk",
        "futuristic alley glowing signs rain night fog",
        "neon lit bridge city skyline rain reflection",
        "cyberpunk market street lights fog rain bokeh",
        "glowing holographic signs wet pavement night",
    ],
    "piano": [
        "single white candle flame dark room warm bokeh",
        "window city night lights rain bokeh dark room",
        "moonlight streaming through sheer curtains floor",
        "close up candle wax melting warm glow dark",
        "window sill rain drops candle reflection night",
        "grand piano keys soft spotlight bokeh",
    ],
    "focus": [
        "bamboo forest morning light rays mist green",
        "shallow stream flowing over mossy rocks peaceful",
        "sunlight filtering through forest canopy leaves rays",
        "zen garden raked sand stone morning mist",
        "mountain valley fog rolling clouds sunrise",
        "japanese garden koi pond water lily reflections",
    ],
}

PIXABAY_API_URL = "https://pixabay.com/api/videos/"


# ─── 画像ダウンロード ───────────────────────────────────────────────

def _get_scene_keywords(genre: str, mood: str) -> list[str]:
    genre_lower = genre.lower()
    for key in SCENE_CATEGORIES:
        if key in genre_lower:
            return SCENE_CATEGORIES[key]
    mood_lower = mood.lower()
    if any(w in mood_lower for w in ["ocean", "wave", "lake", "rain", "water"]):
        return SCENE_CATEGORIES["ambient"]
    if any(w in mood_lower for w in ["cyber", "neon", "city", "urban"]):
        return SCENE_CATEGORIES["cyberpunk"]
    return SCENE_CATEGORIES["lofi"]


def _download_pollinations_image(prompt: str, seed: int, dest: Path) -> bool:
    full_prompt = f"{prompt}, {QUALITY_SUFFIX}"
    url = (
        f"{POLLINATIONS_BASE}/{urllib.parse.quote(full_prompt)}"
        f"?width=1280&height=720&seed={seed}&nologo=true"
        f"&negative={urllib.parse.quote(NEGATIVE_PROMPT)}"
    )
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        if len(r.content) < 5000:
            logger.warning(f"Pollinations レスポンスが小さすぎ: {len(r.content)} bytes")
            return False
        dest.write_bytes(r.content)
        logger.info(f"画像生成完了: seed={seed} ({len(r.content)//1024}KB)")
        return True
    except Exception as e:
        logger.warning(f"Pollinations画像エラー ({prompt[:40]}): {e}")
        return False


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


def _search_pixabay_video(keyword: str, api_key: str) -> str | None:
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
            params["q"] = "nature relaxing"
            r = requests.get(PIXABAY_API_URL, params=params, timeout=30)
            r.raise_for_status()
            hits = r.json().get("hits", [])
        if hits:
            videos = hits[0].get("videos", {})
            for size in ("medium", "large", "small"):
                url = videos.get(size, {}).get("url")
                if url:
                    return url
    except Exception as e:
        logger.warning(f"Pixabay検索エラー: {e}")
    return None


# ─── クリップ生成 ──────────────────────────────────────────────────

def _make_zoompan_clip(
    image_path: Path,
    output_path: Path,
    duration: int,
    zoom_in: bool,
) -> bool:
    """画像に zoompan を適用して動画クリップを生成する"""
    nb_frames = duration * FPS
    if zoom_in:
        z_expr = "min(zoom+0.0003,1.3)"
    else:
        # zoom out: フレーム1で1.3にセットし、毎フレーム減少
        z_expr = "if(eq(on,1),1.3,max(1.001,zoom-0.0003))"

    vf = (
        f"scale=2560:1440,"  # 高解像度で読み込んでzoompanの品質を確保
        f"zoompan="
        f"z='{z_expr}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={nb_frames}:s=1280x720:fps={FPS},"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", str(FPS),
        "-i", str(image_path),
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.error(f"zoompanエラー: {result.stderr[-300:]}")
        return False
    logger.info(f"クリップ生成完了: {output_path.name} ({duration}秒, zoom_{'in' if zoom_in else 'out'})")
    return True


def _build_crossfade_cycle(
    clip_paths: list[Path],
    durations: list[int],
    tmp: Path,
    fade_dur: int = FADE_DUR,
) -> Path | None:
    """複数クリップを xfade で結合してサイクル動画を作成する"""
    if len(clip_paths) == 1:
        return clip_paths[0]

    inputs: list[str] = []
    for cp in clip_paths:
        inputs += ["-i", str(cp)]

    filter_parts: list[str] = []
    prev_label = "[0:v]"
    offset = durations[0] - fade_dur

    for i in range(1, len(clip_paths)):
        transition = random.choice(["dissolve", "fadeblack", "fade"])
        out_label = f"[xf{i}]" if i < len(clip_paths) - 1 else "[vout]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition={transition}"
            f":duration={fade_dur}:offset={max(0, offset)}{out_label}"
        )
        offset += durations[i] - fade_dur
        prev_label = out_label

    total_dur = sum(durations) - fade_dur * (len(clip_paths) - 1)
    cycle_path = tmp / "cycle.mp4"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-t", str(total_dur),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(cycle_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error(f"クロスフェードエラー: {result.stderr[-500:]}")
        return None
    logger.info(f"サイクル動画完成: {total_dur}秒 ({len(clip_paths)}シーン)")
    return cycle_path


# ─── 音楽生成 ──────────────────────────────────────────────────────

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
        "focus": {
            "freqs": [174.6, 220.0, 261.6, 349.2, 440.0],
            "amps":  [0.16,  0.14,  0.12,  0.10,  0.08],
            "lp_cutoff": 4000,
            "echo": "0.85:0.90:100:0.4",
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
    logger.info(f"アンビエント音楽生成完了: {output_path} ({duration_sec}秒)")
    return True


# ─── 最終合成 ──────────────────────────────────────────────────────

def _combine_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    duration_sec: int,
) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
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


# ─── メインエントリ ────────────────────────────────────────────────

def generate_bgm_video(
    concept: dict,
    output_dir: Path,
    duration_sec: int = DEFAULT_DURATION_SEC,
    duration_mode: str = "short",
) -> str | None:
    genre = concept.get("genre", "lofi")
    mood = concept.get("mood", "relaxing calm")
    title_slug = concept.get("title", "bgm")[:30].replace(" ", "_").replace("/", "-")
    output_dir.mkdir(parents=True, exist_ok=True)
    duration_sec = min(duration_sec, 3600)
    output_path = output_dir / f"{title_slug}.mp4"

    if output_path.exists():
        logger.info(f"既存動画を再利用: {output_path}")
        return str(output_path)

    # モード別パラメータ
    if duration_mode == "short":
        n_scenes_range = (3, 3)   # 固定3枚
        clip_dur_range = (20, 20) # 固定20秒
        xfade_dur = 2             # クロスフェード2秒
    else:
        n_scenes_range = (5, 8)
        clip_dur_range = (20, 40)
        xfade_dur = FADE_DUR      # 3秒

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audio_path = tmp / "music.aac"
        cycle_video: Path | None = None

        # Step 1: 音楽生成
        logger.info(f"アンビエント音楽を生成中... ({duration_sec}秒, mode={duration_mode})")
        if not _generate_ambient_audio(audio_path, duration_sec, genre):
            logger.error("音楽生成失敗")
            return None

        # Step 2: Pollinations.AI で画像を複数枚生成してサイクル動画を作る
        scene_keywords = _get_scene_keywords(genre, mood)
        random.shuffle(scene_keywords)
        n_scenes = random.randint(*n_scenes_range)
        n_scenes = min(n_scenes, len(scene_keywords))
        keywords = scene_keywords[:n_scenes]

        # 始点と終点を同じseedにしてシームレスループを設計
        base_seed = random.randint(1000, 9999)
        seeds = [base_seed + i for i in range(n_scenes)]
        seeds[-1] = base_seed  # 最後 = 最初と同じseed

        logger.info(f"Pollinations.AI で {n_scenes} 枚の画像を生成中...")
        image_paths: list[Path] = []
        for idx, (kw, seed) in enumerate(zip(keywords, seeds)):
            img_path = tmp / f"scene_{idx:02d}.jpg"
            if _download_pollinations_image(kw, seed, img_path):
                image_paths.append(img_path)
            else:
                logger.warning(f"シーン{idx}の画像生成スキップ")

        # Step 3: 各画像にzoompanを適用してクリップ化
        if len(image_paths) >= 2:
            clip_paths: list[Path] = []
            durations: list[int] = []
            for idx, img_path in enumerate(image_paths):
                dur = random.randint(*clip_dur_range)
                zoom_in = (idx % 2 == 0)  # 交互にin/out
                clip_path = tmp / f"clip_{idx:02d}.mp4"
                logger.info(f"クリップ生成中: scene_{idx:02d} ({dur}秒, zoom_{'in' if zoom_in else 'out'})")
                if _make_zoompan_clip(img_path, clip_path, dur, zoom_in):
                    clip_paths.append(clip_path)
                    durations.append(dur)
                else:
                    logger.warning(f"クリップ{idx}生成失敗、スキップ")

            if len(clip_paths) >= 2:
                logger.info(f"クロスフェード結合中... ({len(clip_paths)}クリップ, fade={xfade_dur}s)")
                cycle_video = _build_crossfade_cycle(clip_paths, durations, tmp, fade_dur=xfade_dur)

        # Pixabay フォールバック
        if cycle_video is None:
            logger.info("Pollinationsフォールバック: Pixabay動画を試行")
            pixabay_key = os.environ.get("PIXABAY_API_KEY")
            bg_video_path = tmp / "background.mp4"
            if pixabay_key:
                video_url = _search_pixabay_video(mood, pixabay_key)
                if video_url and _download_file(video_url, bg_video_path):
                    cycle_video = bg_video_path

        # 最終フォールバック: 単色グラデーション背景
        if cycle_video is None:
            logger.info("最終フォールバック: グラデーション背景を生成")
            bg_video_path = tmp / "background.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "color=c=0x1a1a2e:size=1280x720:rate=1",
                "-t", "10",
                "-c:v", "libx264",
                str(bg_video_path),
            ]
            subprocess.run(cmd, capture_output=True)
            if bg_video_path.exists():
                cycle_video = bg_video_path

        if cycle_video is None:
            logger.error("背景動画の準備に全て失敗")
            return None

        # Step 4: サイクル動画 + 音楽を合成（stream_loopで1時間にループ）
        logger.info(f"最終合成中: {cycle_video.name} x loop -> {duration_sec}秒")
        if not _combine_video_audio(cycle_video, audio_path, output_path, duration_sec):
            return None

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(f"BGM動画生成完了: {output_path} ({size_mb:.1f} MB)")
    return str(output_path)
