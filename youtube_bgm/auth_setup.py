"""
auth_setup.py: YouTube OAuth2 認証セットアップ
client_secret.json を配置後に実行してトークンを生成する

使い方:
  python youtube_bgm/auth_setup.py
"""
import os
import pickle
from pathlib import Path

CREDENTIALS_DIR = Path(__file__).parent / ".credentials"
SECRET_FILE = CREDENTIALS_DIR / "client_secret.json"
TOKEN_FILE = CREDENTIALS_DIR / "youtube_token.pkl"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def main():
    if not SECRET_FILE.exists():
        print(f"ERROR: {SECRET_FILE} が見つかりません。")
        print("Google Cloud Console から client_secret.json をダウンロードして配置してください。")
        return

    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRET_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
        print(f"トークン保存完了: {TOKEN_FILE}")

    print("\n=== GitHub Secrets 登録用の値 ===")
    print(f"YOUTUBE_CLIENT_ID:     {creds.client_id}")
    print(f"YOUTUBE_CLIENT_SECRET: {creds.client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN: {creds.refresh_token}")
    print("\nこれらの値を TASK 5 で GitHub Secrets に登録してください。")


if __name__ == "__main__":
    main()
