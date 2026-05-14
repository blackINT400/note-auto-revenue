#!/usr/bin/env python3
"""
setup.py: 初回セットアップチェックスクリプト
実行: python setup.py
"""
import os
import sys
from pathlib import Path


def check():
    errors = []
    warnings = []

    print("=" * 55)
    print("  Zenn自動収益システム セットアップチェック")
    print("=" * 55)

    # ── ディレクトリ作成 ──
    for d in ["agents", "data", "logs", "articles", ".github/workflows"]:
        Path(d).mkdir(parents=True, exist_ok=True)
    Path("agents/__init__.py").touch(exist_ok=True)
    print("✅ ディレクトリ構造を確認しました")

    # ── Pythonバージョン ──
    ver = sys.version_info
    if ver < (3, 11):
        errors.append(f"Python 3.11以上が必要です（現在: {ver.major}.{ver.minor}）")
    else:
        print(f"✅ Python {ver.major}.{ver.minor}.{ver.micro}")

    # ── パッケージ確認 ──
    packages = {
        "anthropic": "anthropic",
        "feedparser": "feedparser",
        "yaml": "pyyaml",
        "pytrends": "pytrends",
        "requests": "requests",
    }
    for module, pkg in packages.items():
        try:
            __import__(module)
            print(f"✅ {pkg}")
        except ImportError:
            errors.append(f"{pkg} が未インストール → pip install {pkg}")

    # ── 環境変数 ──
    if not os.environ.get("ANTHROPIC_API_KEY"):
        errors.append("ANTHROPIC_API_KEY が未設定\n"
                       "   → .env.example を .env にコピーして値を記入するか、"
                       "     GitHub Secrets に登録してください")
    else:
        key = os.environ["ANTHROPIC_API_KEY"]
        if not key.startswith("sk-ant-"):
            warnings.append("ANTHROPIC_API_KEY の形式が正しくない可能性があります")
        else:
            print("✅ ANTHROPIC_API_KEY")

    if not os.environ.get("SLACK_WEBHOOK_URL"):
        warnings.append("SLACK_WEBHOOK_URL が未設定（Slack通知なしで動作します）")
    else:
        print("✅ SLACK_WEBHOOK_URL")

    # ── config.yaml ──
    config_path = Path("config.yaml")
    if not config_path.exists():
        errors.append("config.yaml が見つかりません")
    else:
        try:
            import yaml
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if not config.get("niche"):
                warnings.append("config.yaml の niche が空です")
            print(f"✅ config.yaml (niche: {config.get('niche', '未設定')})")
        except Exception as e:
            errors.append(f"config.yaml の読み込みエラー: {e}")

    # ── 結果表示 ──
    print()
    if warnings:
        print("【警告】")
        for w in warnings:
            print(f"  ⚠️  {w}")
        print()

    if errors:
        print("【エラー（要対応）】")
        for e in errors:
            print(f"  ❌ {e}")
        print()
        print("上記エラーを解消してから再実行してください。")
        sys.exit(1)

    print("すべてのチェックが通りました！")
    print()
    print("【次のステップ】")
    print("  1. config.yaml の niche・articles_per_day を確認")
    print("  2. GitHubでプライベートリポジトリを作成してこのフォルダをプッシュ")
    print("  3. GitHub Secrets に ANTHROPIC_API_KEY を登録")
    print("  4. Zennの設定 (zenn.dev) でGitHubリポジトリを連携")
    print("  5. GitHub Actions → Workflows で 'Zenn Auto Publisher' を有効化")
    print()
    print("ローカルテスト用コマンド:")
    print("  python main.py --mode daily   # 日次処理をテスト")
    print("  python main.py --mode weekly  # 週次分析をテスト")


if __name__ == "__main__":
    check()
