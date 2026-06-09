"""
youtube_uploader.py: YouTube Data API v3 アップロードエージェント
動画パッケージのメタデータをYouTubeにアップロードする

Dry-runモードでは実際のアップロードをスキップする。
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


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

        # 実際の動画ファイルパスを探す（パッケージに含まれていれば使用、なければテスト動画）
        video_file = package.get("video_file_path")
        if not video_file or not Path(video_file).exists():
            video_file = os.environ.get("YOUTUBE_TEST_VIDEO", "/tmp/test_upload.mp4")

        if not Path(video_file).exists():
            return {"success": False, "error": f"動画ファイルが見つかりません: {video_file}"}

        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(video_file, mimetype="video/mp4", resumable=True)

        response = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        ).execute()
        video_id = response.get("id")
        logger.info(f"アップロード完了: https://youtu.be/{video_id}")
        return {"success": True, "video_id": video_id, "title": package["title"]}
    except Exception as e:
        logger.error(f"アップロードエラー: {e}")
        return {"success": False, "error": str(e)}
