"""
video_generator.py: 窓×自然×暖炉 高級リビングルーム映像生成

処理:
  1. Pollinations.AI で高級リビングルーム4シーンを生成
  2. 各画像に超低速zoompan(z+0.0002)を適用
  3. xfadeクロスフェード結合
  4. チャンネル名テキストオーバーレイ(5秒後フェードイン)
  5. アンビエント音楽と合成
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
FADE_DUR = 3
FPS = 24
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

NEGATIVE_PROMPT = "text, watermark, people, faces, hands, logos, words, signature, ugly, blurry, cartoon, anime"

# 窓×自然×暖炉 高級リビングルームシーン
LUXURY_SCENES = [
    (
        "morning_forest",
        "luxury modern living room, floor-to-ceiling windows, "
        "japanese forest and lake view outside, morning sunlight, "
        "Eames lounge chair, indoor plants, warm wood interior, "
        "birds and leaves moving outside, 4K cinematic, no people",
    ),
    (
        "evening_ocean",
        "ultra-luxury cliff villa, panoramic floor-to-ceiling windows, "
        "ocean sunset view, hanging fireplace with flames, "
        "Eames chair, minimal luxury furniture, "
        "golden hour reflections on water, cinematic, no people",
    ),
    (
        "night_mountain",
        "cozy luxury chalet, large windows, "
        "snowy mountain forest at night, warm fireplace glow inside, "
        "reading nook, warm amber lighting, stars visible outside, "
        "cinematic depth of field, no people",
    ),
    (
        "rainy_cafe",
        "luxury indoor jazz cafe by the window, "
        "rain drops on large glass windows, green forest outside, "
        "coffee cups, bookshelf, warm lamp light, "
        "cozy intimate atmosphere, 4K, no people",
    ),
]

PIXABAY_API_URL = "https://pixabay.com/api/videos/"


# ─── 画像ダウンロード ──────────────────────────────────────────────

def _get_luxury_scenes(n: int) -> tuple[list[str], list[int]]:
    """n枚のシーンを選抜し、プロンプトとseedリストを返す"""
    selected = random.sample(LUXURY_SCENES, min(n, len(LUXURY_SCENES)))
    base_seed = random.randint(1000, 9999)
    seeds = [base_seed + i for i in range(len(selected))]
    seeds[-1] = base_seed  # 最後 = 最初と同じseedでシームレスループ
    prompts = [p for _, p in selected]
    return prompts, seeds


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


# ─── クリップ生成 ──────────────────────────────────────────────

def _make_zoompan_clip(
    image_path: Path,
    output_path: Path,
    duration: int,
    zoom_in: bool,
) -> bool:
    """超低速zoompanクリップを生成"""
    nb_frames = duration * FPS
    if zoom_in:
        z_expr = "min(zoom+0.0002,1.2)"
    else:
        z_expr = "if(eq(on,1),1.2,max(1.001,zoom-0.0002))"

    vf = (
        f"scale=2560:1440,"
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


# ─── 音楽生成 ──────────────────────────────────────────────

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


# ─── 最終合成 ──────────────────────────────────────────────

def _combine_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    duration_sec: int,
    channel_name: str = "",
) -> bool:
    """stream_loopでループし、チャンネル名テキストオーバーレイを付加して最終動画を出力"""
    base_vf = (
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )

    if channel_name:
        safe_name = (
            channel_name
            .replace("\\", "")
            .replace("'", "")
            .replace(":", "")
            .replace(",", "")
            .replace("[", "").replace("]", "")
        )
        alpha = (
            "if(lt(t\\,5)\\,0\\"
            ",if(lt(t\\,8)\\,(t-5)/3*0.8\\"
            ",if(lt(t\\,11)\\,0.8\\"
            ",if(lt(t\\,14)\\,(14-t)/3*0.8\\,0))))"
        )
        drawtext = (
            f"drawtext="
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            f":text='{safe_name}'"
            f":fontsize=52"
            f":fontcolor=white"
            f":x=(w-text_w)/2"
            f":y=(h-text_h)/2"
            f":alpha='{alpha}'"
        )
        vf = f"{base_vf},{drawtext}"
    else:
        vf = base_vf

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-vf", vf,
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
        if channel_name:
            logger.warning("テキストなしで再実行...")
            return _combine_video_audio(video_path, audio_path, output_path, duration_sec, channel_name="")
        return False
    logger.info(f"動画合成完了: {output_path}")
    return True


# ─── メインエントリ ────────────────────────────────────────────

def generate_bgm_video(
    concept: dict,
    output_dir: Path,
    duration_sec: int = DEFAULT_DURATION_SEC,
    duration_mode: str = "short",
    channel_name: str = "",
) -> str | None:
    genre = concept.get("genre", "lofi")
    title_slug = concept.get("title", "bgm")[:30].replace(" ", "_").replace("/", "-")
    output_dir.mkdir(parents=True, exist_ok=True)
    duration_sec = min(duration_sec, 7200)
    output_path = output_dir / f"{title_slug}.mp4"

    if output_path.exists():
        logger.info(f"既存動画を再利用: {output_path}")
        return str(output_path)

    if duration_mode == "short":
        n_scenes = 3
        clip_dur_range = (20, 20)
        xfade_dur = 2
    elif duration_mode == "medium":
        n_scenes = 4
        clip_dur_range = (25, 35)
        xfade_dur = 3
    else:
        n_scenes = 4
        clip_dur_range = (25, 40)
        xfade_dur = 3

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audio_path = tmp / "music.aac"
        cycle_video: Path | None = None

        logger.info(f"アンビエント音楽を生成中... ({duration_sec}秒, mode={duration_mode})")
        if not _generate_ambient_audio(audio_path, duration_sec, genre):
            logger.error("音楽生成失敗")
            return None

        prompts, seeds = _get_luxury_scenes(n_scenes)
        logger.info(f"Pollinations.AI で {len(prompts)} 枚の画像を生成中...")
        image_paths: list[Path] = []
        for idx, (prompt, seed) in enumerate(zip(prompts, seeds)):
            img_path = tmp / f"scene_{idx:02d}.jpg"
            if _download_pollinations_image(prompt, seed, img_path):
                image_paths.append(img_path)
            else:
                logger.warning(f"シーン{idx}の画像生成スキップ")

        if len(image_paths) >= 2:
            clip_paths: list[Path] = []
            durations: list[int] = []
            for idx, img_path in enumerate(image_paths):
                dur = random.randint(*clip_dur_range)
                zoom_in = (idx % 2 == 0)
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

        if cycle_video is None:
            logger.info("Pollinationsフォールバック: Pixabay動画を試行")
            pixabay_key = os.environ.get("PIXABAY_API_KEY")
            bg_video_path = tmp / "background.mp4"
            if pixabay_key:
                video_url = _search_pixabay_video("luxury interior fireplace", pixabay_key)
                if video_url and _download_file(video_url, bg_video_path):
                    cycle_video = bg_video_path

        if cycle_video is None:
            logger.info("最終フォールバック: グラデーション背景を生成")
            bg_video_path = tmp / "background.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=0x1a1a2e:size=1280x720:rate=1",
                "-t", "10", "-c:v", "libx264", str(bg_video_path),
            ], capture_output=True)
            if bg_video_path.exists():
                cycle_video = bg_video_path

        if cycle_video is None:
            logger.error("背景動画の準備に全て失敗")
            return None

        logger.info(f"最終合成中: {cycle_video.name} -> {duration_sec}秒 / channel={channel_name!r}")
        if not _combine_video_audio(cycle_video, audio_path, output_path, duration_sec, channel_name=channel_name):
            return None

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(f"BGM動画生成完了: {output_path} ({size_mb:.1f} MB)")
    return str(output_path)
