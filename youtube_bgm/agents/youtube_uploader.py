"""
youtube_uploader.py: YouTube Data API v3 アップロードエージェント
動画パッケージのメタデータをYouTubeにアップロードする

Dry-runモードでは実際のアップロードをスキップする。
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


_CREDENTIALS_DIR = Path(__file__).parent.parent / ".credentials"
_TOKEN_PKL = _CREDENTIALS_DIR / "youtube_token.pkl"
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
           "https://www.googleapis.com/auth/youtube"]


def _get_youtube_client():
    """OAuth2認証済みYouTubeクライアントを返す。
    優先順位:
      1. 環境変数 YOUTUBE_CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN (GitHub Actions)
      2. .credentials/youtube_token.pkl (ローカル開発)
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as e:
        raise ImportError(f"google-api-python-client が未インストール: {e}") from e

    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=_SCOPES,
        )
        creds.refresh(Request())
    elif _TOKEN_PKL.exists():
        import pickle
        with open(_TOKEN_PKL, "rb") as f:
            creds = pickle.load(f)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    else:
        raise RuntimeError(
            "YouTube 認証情報が見つかりません。\n"
            "環境変数 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN を設定するか、\n"
            "python youtube_bgm/auth_setup.py を実行してください。"
        )

    return build("youtube", "v3", credentials=creds)


_AI_DISCLOSURE = "\n\n※この動画の映像・音楽はAIで生成されています"
_AI_TAGS = ["AI生成", "AI BGM"]

_HASHTAGS = (
    "\n\n"
    "#作業BGM #勉強BGM #集中BGM #睡眠BGM #カフェBGM "
    "#深夜作業 #lofi #jazz #bgm #relax "
    "#study #studymusic #lofihiphop #chillmusic #ambientmusic "
    "#작업할때듣는음악 #공부할때듣는음악 #집중력 #relaxingmusic #bgmmusic"
)

_CTA = (
    "\n\n"
    "▶ チャンネル登録で毎日新しいBGMをお届けします！\n"
    "🔔 通知をオンにして聴き逃しなし。毎日投稿中。"
)


def _build_description_footer(description: str) -> str:
    if _CTA not in description:
        description += _CTA
    if _AI_DISCLOSURE not in description:
        description += _AI_DISCLOSURE
    if _HASHTAGS not in description:
        description += _HASHTAGS
    return description


def _build_body(package: dict) -> dict:
    description = _build_description_footer(package.get("description", ""))

    tags = package.get("tags", [])
    for tag in _AI_TAGS:
        if tag not in tags:
            tags = tags + [tag]

    return {
        "snippet": {
            "title": package["title"],
            "description": description,
            "tags": tags,
            "categoryId": package.get("category_id", "10"),
        },
        "status": {
            "privacyStatus": package.get("privacy_status", "private"),
            "containsSyntheticMedia": True,
        },
    }


def _notify_discord(package: dict, video_id: str) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_YOUTUBE")
    if not webhook_url:
        return
    try:
        genre = package.get("visual_concept", "").split(":")[0].strip() or "lofi"
        cost = package.get("cost_jpy", 0)
        now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        content = (
            "🎬 **新しい動画を投稿しました！**\n\n"
            f"**タイトル**: {package.get('title', '')}\n"
            f"**URL**: https://youtu.be/{video_id}\n"
            f"**ジャンル**: {genre}\n"
            f"**コスト**: {cost}円\n"
            f"**投稿時刻**: {now_jst}"
        )
        requests.post(
            webhook_url,
            json={"content": content},
            timeout=10,
        )
        logger.info("Discord通知送信完了")
    except Exception as e:
        logger.warning(f"Discord通知失敗（アップロードは成功）: {e}")


def upload_video_metadata(package: dict, dry_run: bool = False) -> dict:
    """動画メタデータをYouTubeにアップロード（dry_run=TrueならスキップしてOK返す）"""
    if dry_run:
        logger.info(f"[DRY-RUN] アップロードスキップ: {package.get('title')}")
        return {
            "success": True,
            "dry_run": True,
            "video_id": "dry_run_video_id",
            "title": package.get("title"),
            "message": "Dry-run: アップロードはスキップされました",
        }

    try:
        youtube = _get_youtube_client()
        body = _build_body(package)
        response = youtube.videos().insert(
            part="snippet,status",
            body=body,
            # media_body は実際の動画ファイルが必要 — ここではメタデータのみ
        ).execute()
        video_id = response.get("id")
        logger.info(f"アップロード完了: https://youtu.be/{video_id}")
        result = {"success": True, "video_id": video_id, "title": package["title"]}
        _notify_discord(package, video_id)
        return result
    except Exception as e:
        logger.error(f"アップロードエラー: {e}")
        return {"success": False, "error": str(e)}
