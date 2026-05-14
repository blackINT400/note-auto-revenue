# Zenn 自動収益システム

Claude APIとGitHub Actionsを使って、毎日自動でZennに記事を投稿するシステムです。  
設定は初回1回のみ。以降は完全自動で動きます。

---

## 仕組み

```
毎日 06:00 JST（GitHub Actions）
  ↓
[Scout]     はてブRSS + Google Trendsでトレンド収集
  ↓
[Writer]    Claude APIでZenn記事を自動生成（品質チェック付き）
  ↓
[Publisher] articles/ に保存 → GitHubにコミット → Zennが自動公開
  ↓
毎週月曜 08:00 JST
  ↓
[Analyst]   投稿ログを分析 → config.yaml の戦略を自動更新
```

---

## 収益モデルについて

Zennには有料記事機能がありません。  
このシステムは **記事×アフィリエイト** モデルで収益を目指します。

- Zennで継続的に高品質記事を投稿 → 検索流入・フォロワー増加
- 記事内のアフィリエイトリンク（Amazon・A8.net等）から収益
- Zennの「サポート」機能（投げ銭）

---

## 必要なもの（すべて無料）

| 項目 | 取得場所 |
|------|----------|
| GitHubアカウント | github.com |
| Anthropic APIキー | console.anthropic.com |
| Zennアカウント | zenn.dev |
| Slack Webhook URL（任意） | api.slack.com/apps |

---

## セットアップ手順

### ステップ1: GitHubにリポジトリを作成

1. github.com にログイン
2. 右上「＋」→「New repository」
3. リポジトリ名: `zenn-auto-revenue`（**Private** を選択）
4. このフォルダをそのままアップロード

### ステップ2: GitHub Secretsに登録

リポジトリの `Settings` → `Secrets and variables` → `Actions` → `New repository secret`:

| Secret名 | 値 |
|----------|----|
| `ANTHROPIC_API_KEY` | console.anthropic.com で取得 |
| `SLACK_WEBHOOK_URL` | Slackアプリ設定で取得（省略可） |

### ステップ3: config.yaml を編集

```yaml
niche: "副業・節税"      ← 書きたいジャンルに変更
articles_per_day: 2      ← 1日の記事数（2推奨）
monthly_cost_limit: 2000 ← APIコスト上限（円）
quality_threshold: 70    ← 品質スコア閾値（0-100）
```

### ステップ4: ZennとGitHubを連携

1. zenn.dev にログイン
2. 「GitHubからデプロイ」→ 作成したリポジトリを選択
3. 以降、`articles/` に追加されたmdファイルが自動公開されます

### ステップ5: GitHub Actionsを有効化

リポジトリの `Actions` タブ → `Zenn Auto Publisher` → `Enable workflow`

**以上で完了です。翌朝06:00から自動で動き始めます。**

---

## ファイル構成

```
zenn-auto-revenue/
├── config.yaml              # あなたが設定（初回のみ）
├── main.py                  # 全体統括・リトライ・Slack通知
├── requirements.txt
├── setup.py                 # 初回チェックスクリプト
├── .env.example             # ローカルテスト用環境変数サンプル
├── agents/
│   ├── scout.py             # トレンド収集
│   ├── writer.py            # 記事生成 + コスト管理
│   ├── publisher.py         # Zennファイル生成
│   └── analyst.py           # 週次分析・戦略更新
├── articles/                # Zennが読む記事ファイル（自動生成）
├── data/                    # トレンドデータ・コスト記録（自動生成）
├── logs/                    # 実行ログ・投稿履歴（自動生成）
└── .github/workflows/
    └── daily.yml            # スケジュール設定
```

---

## 安全装置

| 装置 | 内容 |
|------|------|
| **APIコスト上限** | 月2000円（変更可）を超えた瞬間に停止 |
| **重複防止** | タイトルのSHA-256ハッシュで二重投稿をブロック |
| **品質チェック** | スコア70点未満は最大2回再生成 |
| **エラー停止** | 同一処理で3回連続エラー → Slack通知 + 停止 |
| **[skip ci]** | 自動コミットが無限ループしないよう制御 |

---

## ローカルでのテスト方法

```bash
# 1. 依存パッケージをインストール
pip install -r requirements.txt

# 2. 環境変数を設定
cp .env.example .env
# .envを開いてAPIキーを記入

# 3. セットアップチェック
python setup.py

# 4. 日次処理をテスト
python main.py --mode daily

# 5. 週次分析をテスト
python main.py --mode weekly
```

---

## よくある質問

**Q: APIコストはどのくらいかかりますか？**  
A: 1記事あたり約5〜10円。1日2記事×30日=約300〜600円/月。月2000円上限で確実に止まります。

**Q: Zennのどのジャンルでも使えますか？**  
A: config.yamlのnicheを変えるだけで対応できます。

**Q: 記事の品質が低くないですか？**  
A: quality_thresholdで品質基準を設定し、未達なら再生成します。最終的な確認は月1回ログを見てください。
