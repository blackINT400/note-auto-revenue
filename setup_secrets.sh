#!/usr/bin/env bash
set -e

REPO="blackINT400/note-auto-revenue"

echo "================================================"
echo " GitHub Secrets セットアップ"
echo " リポジトリ: $REPO"
echo "================================================"
echo ""

# --- Step 1: PAT 認証 ---
if ! gh auth status &>/dev/null; then
  echo "【Step 1】GitHub Personal Access Token (PAT) で認証します"
  echo ""
  echo "PAT の取得手順:"
  echo "  1. https://github.com/settings/tokens/new を開く"
  echo "  2. Note: youtube-bgm-secrets"
  echo "  3. Expiration: 90 days"
  echo "  4. Scopes: [✓] repo  (repo:全部にチェック)"
  echo "  5. 「Generate token」→ トークンをコピー"
  echo ""
  printf "PAT を貼り付けてください (入力は非表示): "
  read -rs GH_TOKEN
  echo ""
  echo "$GH_TOKEN" | gh auth login --with-token
  echo "✅ GitHub 認証完了"
else
  echo "✅ GitHub 認証済み (スキップ)"
fi

echo ""
echo "================================================"
echo " Secrets を対話形式で登録します"
echo " （空Enter でスキップ、後で再実行できます）"
echo "================================================"
echo ""

set_secret() {
  local name="$1"
  local hint="$2"
  printf "[%s]\n  %s\n  値を入力 (空でスキップ): " "$name" "$hint"
  read -rs value
  echo ""
  if [ -n "$value" ]; then
    echo "$value" | gh secret set "$name" --repo "$REPO"
    echo "  ✅ $name を登録しました"
  else
    echo "  ⏭  $name をスキップしました"
  fi
  echo ""
}

set_secret "ANTHROPIC_API_KEY" \
  "取得元: https://console.anthropic.com/ → API Keys"

set_secret "PIXABAY_API_KEY" \
  "取得元: https://pixabay.com/api/docs/ (ログイン後ページ上部に表示)"

set_secret "DISCORD_WEBHOOK_YOUTUBE" \
  "取得元: Discord → チャンネル設定 → 連携サービス → ウェブフック URL"

set_secret "YOUTUBE_CLIENT_ID" \
  "取得元: python youtube_bgm/auth_setup.py を実行して表示された CLIENT_ID"

set_secret "YOUTUBE_CLIENT_SECRET" \
  "取得元: python youtube_bgm/auth_setup.py を実行して表示された CLIENT_SECRET"

set_secret "YOUTUBE_REFRESH_TOKEN" \
  "取得元: python youtube_bgm/auth_setup.py を実行して表示された REFRESH_TOKEN"

echo "================================================"
echo " 登録済みSecrets一覧:"
gh secret list --repo "$REPO"
echo "================================================"
echo ""
echo "🎉 セットアップ完了！"
echo "本番テスト投稿:"
echo "  python youtube_bgm/pipeline.py --genre \"cozy indoor jazz cafe night\""
