"""
youtube_uploader.py: YouTube Data API v3 アップロードエージェント
動画パッケージのメタデータをYouTubeにアップロードする

Dry-runモードでは実際のアップロードをスキップする。
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

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


def _get_youtube_client():
    """OAuth2認証済みYouTubeクライアントを返す"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise ImportError(f"google-api-python-client が未インストール: {e}") from e

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
              "https://www.googleapis.com/auth/youtube"]
    token_path = Path("token.json")
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secret_file = os.environ.get("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
            flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds)


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
        return {"success": True, "video_id": video_id, "title": package["title"]}
    except Exception as e:
        logger.error(f"アップロードエラー: {e}")
        return {"success": False, "error": str(e)}
