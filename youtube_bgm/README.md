# YouTube BGM Pipeline

AI駆動のYouTube BGMチャンネル自動運営システム。

## アーキテクチャ

```
pipeline.py (オーケストレーター)
  ├── agents/trend_researcher.py   # トレンドBGMジャンル調査
  ├── agents/music_composer.py     # 楽曲プロンプト・構成生成
  ├── agents/video_assembler.py    # 動画パッケージ生成
  └── agents/youtube_uploader.py  # YouTube Data API v3 アップロード
```

## セットアップ

```bash
pip install -r requirements.txt
```

### 環境変数

```
ANTHROPIC_API_KEY=your_key
YOUTUBE_CLIENT_SECRET_FILE=client_secret.json  # OAuth2
```

## 実行

```bash
# ドライラン（実際のアップロードなし）
python pipeline.py --dry-run

# 本番実行
python pipeline.py

# モード指定
python pipeline.py --mode weekly
```

## GitHub Actions

`.github/workflows/youtube_bgm_pipeline.yml` が毎日 09:00 JST に自動実行。
