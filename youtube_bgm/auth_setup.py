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
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            import sys
            import urllib.request
            import urllib.parse
            import json
            import secrets as _secrets

            with open(SECRET_FILE) as f:
                client_config = json.load(f)
            cfg = client_config.get("installed") or client_config.get("web")
            client_id = cfg["client_id"]
            client_secret = cfg["client_secret"]
            token_uri = cfg["token_uri"]

            # --code が引数で渡された場合はトークン交換のみ行う
            code_arg = None
            for arg in sys.argv[1:]:
                if arg.startswith("--code="):
                    code_arg = arg.split("=", 1)[1]

            if code_arg:
                data = urllib.parse.urlencode({
                    "code": code_arg,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "authorization_code",
                }).encode()
                req = urllib.request.Request(token_uri, data=data)
                with urllib.request.urlopen(req) as resp:
                    token_data = json.loads(resp.read())

                # Credentials オブジェクトを手動構築
                from google.oauth2.credentials import Credentials
                creds = Credentials(
                    token=token_data["access_token"],
                    refresh_token=token_data.get("refresh_token"),
                    token_uri=token_uri,
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=SCOPES,
                )
            else:
                auth_url = (
                    "https://accounts.google.com/o/oauth2/auth"
                    f"?response_type=code"
                    f"&client_id={urllib.parse.quote(client_id)}"
                    f"&redirect_uri=urn%3Aietf%3Awg%3Aoauth%3A2.0%3Aoob"
                    f"&scope={urllib.parse.quote(' '.join(SCOPES))}"
                    f"&access_type=offline"
                    f"&prompt=consent"
                )
                print("\n以下のURLをブラウザで開いて認証してください:")
                print(f"\n  {auth_url}\n")
                print("認証後、表示されたコードを使って以下を実行してください:")
                print(f"  python youtube_bgm/auth_setup.py --code=<コード>\n")
                return
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
