# ARASAKA BRAIN

フラクタル7機能構造による自律収益化システム。

## 絶対ルール
1. APIコストは月$6.5（¥1,000）上限。超えそうな処理は cost_tracker で事前チェック
2. モデルは claude-haiku-4-5-20251001 をデフォルトとする
3. 即時性不要なタスクは必ず Batch API を使う
4. 重大な変更・コスト超過リスクは Discord に通知してから実行

## 新プロジェクト追加手順
1. `projects/_template/` をコピーして `projects/<name>/` を作成
2. `config.yaml` にゴール・KPI・SL条件を記載
3. `executor.py` で ExecutorAgent を継承して専門化
4. `brain.py` から `python brain.py --project <name>` で呼び出せる

## 7機能テンプレート
`org_template.yaml` を参照。全PJはこの構造に従う。

## フラクタル7機能
| # | 機能 | エージェント | inputs | outputs |
|---|---|---|---|---|
| ① | 戦略・設計 | ArchitectAgent | project_goal, constraints | roadmap, kpi_definitions, sl_conditions |
| ② | 調達・情報 | ResearcherAgent | research_query, depth | market_data, competitor_analysis, raw_materials |
| ③ | 生産・実行 | ExecutorAgent(継承) | instructions, materials | artifacts |
| ④ | 販売・配布 | PublisherAgent | artifacts, seo_keywords | published_urls, distribution_report |
| ⑤ | 財務・KPI | MonitorAgent | kpi_definitions, published_urls | kpi_report, revenue_data |
| ⑥ | 学習・改善 | LearnerAgent | kpi_report, artifacts | improvement_suggestions, updated_prompts |
| ⑦ | 統治・判断 | GovernorAgent | all_reports, cost_data | decisions, sovereign_requests |

## ディレクトリ構成
```
arasaka-brain/
├── brain.py           # エントリポイント: python brain.py --project <name> --mode <mode>
├── config.yaml        # グローバル設定（コスト上限・モデル）
├── org_template.yaml  # フラクタルテンプレート定義
├── shared/            # 共通ライブラリ（コスト・Discord・ログ）
├── agents/            # 7機能エージェント群
└── projects/          # PJ別実装
    ├── note_zenn/     # noteマガジン + Zenn記事
    ├── fx_mt4/        # FXトレード分析
    └── _template/     # 新PJ追加用テンプレート
```

## コスト管理
- 月上限: ¥1,000（$6.5）
- 80%到達でDiscordアラート
- 100%到達で全処理SL停止
- コストログ: `.cost_log.json`（月次リセット）
