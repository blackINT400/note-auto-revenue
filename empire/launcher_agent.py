"""
launcher_agent.py: 新事業を自動セットアップするエージェント

フロー:
  1. scout_agent からの機会情報を受け取る
  2. 最適な playbook を選択
  3. Claude API で新事業の設定・スクリプト・GitHub Actions を生成
  4. portfolio.yaml に status: "pending_human_approval" で登録
  5. Slack に承認リクエストを送信

安全装置:
  - 起動は必ずオーナー承認後（Slackで通知、GitHub Actions側で手動実行）
  - 1日1件制限
"""
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import anthropic
import yaml

from empire.utils import (
    EMPIRE_DIR, PROJECT_ROOT,
    get_model, load_portfolio, record_empire_cost, save_portfolio,
)

logger = logging.getLogger(__name__)

PLAYBOOKS_DIR = EMPIRE_DIR / "playbooks"
BUSINESSES_DIR = PROJECT_ROOT / "businesses"


# ── プレイブック選択 ──────────────────────────────────────────────────────────

def _select_playbook(opportunity: dict) -> dict:
    """推薦プラットフォームに基づいてプレイブックを選択する"""
    platform = opportunity.get("recommended_platform", "").lower()
    playbook_map = {
        "wordpress": "affiliate_seo",
        "gumroad": "gumroad_product",
        "api": "api_service",
        "zenn": "affiliate_seo",   # ZennはSEO記事と相性が良い
    }
    playbook_id = playbook_map.get(platform, "affiliate_seo")
    playbook_path = PLAYBOOKS_DIR / f"{playbook_id}.yaml"

    if not playbook_path.exists():
        logger.warning(f"Playbook '{playbook_id}' が見つかりません。affiliate_seo を使用します。")
        playbook_path = PLAYBOOKS_DIR / "affiliate_seo.yaml"

    return yaml.safe_load(playbook_path.read_text(encoding="utf-8"))


# ── ファイル生成（Claude API） ────────────────────────────────────────────────

def _generate_config(opportunity: dict, playbook: dict,
                     client: anthropic.Anthropic, model: str) -> str:
    """新事業の config.yaml を Claude で生成する"""
    niche = opportunity.get("recommended_niche", opportunity.get("opportunity", "")[:30])
    platform = playbook.get("name", "")

    prompt = f"""以下の情報をもとに、新事業の config.yaml を生成してください。

【事業タイプ】{platform}
【ニッチ】{niche}
【収益モデル】{playbook.get("revenue_model", "")}
【推奨理由】{opportunity.get("reason", "")}

config.yaml の内容（YAMLのみ出力してください）:
- niche: "{niche}"
- platform: "{playbook.get('id', 'affiliate_seo')}"
- articles_per_day: 2
- monthly_cost_limit: 1000
- quality_threshold: 70
- model: "claude-sonnet-4-6"
- revenue_model: "{playbook.get('revenue_model', '')}"
- required_accounts: {json.dumps(playbook.get("required_accounts", []), ensure_ascii=False)}
- auto_strategy:
    top_keywords: []
    best_style: ""
    last_updated: ""

---YAMLのみ出力してください（説明文は不要）---"""

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    config_yaml = response.content[0].text.strip()
    record_empire_cost("launcher", response.usage.input_tokens, response.usage.output_tokens)

    # YAMLコードブロックを除去
    config_yaml = re.sub(r"^```(?:yaml)?\s*", "", config_yaml, flags=re.MULTILINE)
    config_yaml = re.sub(r"\s*```$", "", config_yaml, flags=re.MULTILINE)
    return config_yaml.strip()


def _generate_setup_guide(opportunity: dict, playbook: dict,
                          client: anthropic.Anthropic, model: str) -> str:
    """新事業のセットアップガイド（SETUP.md）を Claude で生成する"""
    prompt = f"""以下の新事業のセットアップ手順を日本語のMarkdownで作成してください。

【事業タイプ】{playbook.get("name", "")}
【ニッチ】{opportunity.get("recommended_niche", "")}
【必要なアカウント】{json.dumps(playbook.get("required_accounts", []), ensure_ascii=False)}
【セットアップ手順】{json.dumps(playbook.get("setup_steps", []), ensure_ascii=False)}
【必要な環境変数】{json.dumps(playbook.get("required_env_vars", []), ensure_ascii=False)}
【注意事項】{playbook.get("caution", "")}

300字以内で簡潔にまとめてください。"""

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    guide = response.content[0].text.strip()
    record_empire_cost("launcher", response.usage.input_tokens, response.usage.output_tokens)
    return guide


def _generate_github_actions(business_id: str, playbook: dict) -> str:
    """新事業の GitHub Actions ワークフローを生成する"""
    return f"""name: {business_id} Auto Runner

on:
  schedule:
    - cron: '0 22 * * *'   # 毎日 07:00 JST
  workflow_dispatch:
    inputs:
      mode:
        description: '実行モード'
        required: true
        default: 'daily'
        type: choice
        options: [daily, weekly]

jobs:
  run:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: |
          pip install anthropic feedparser pyyaml requests python-dotenv

      - name: Run business
        env:
          ANTHROPIC_API_KEY: ${{{{ secrets.ANTHROPIC_API_KEY }}}}
          SLACK_WEBHOOK_URL: ${{{{ secrets.SLACK_WEBHOOK_URL }}}}
        run: python businesses/{business_id}/main.py --mode ${{{{ inputs.mode || 'daily' }}}}

      - name: Commit results
        run: |
          git config --global user.email "auto@empire.bot"
          git config --global user.name "Empire Bot"
          git add businesses/{business_id}/
          git diff --staged --quiet || git commit -m "auto: {business_id} $(date +%Y-%m-%d) [skip ci]"
          git push
"""


def _generate_main_script(opportunity: dict, playbook: dict) -> str:
    """新事業の main.py を生成する（プレイブック統一インターフェースを使用）"""
    niche = opportunity.get("recommended_niche", "")
    playbook_id = playbook.get("id", "affiliate_seo")
    name = playbook.get("name", "新事業")
    revenue_model = playbook.get("revenue_model", "")

    return f'''#!/usr/bin/env python3
"""
{name} - メインスクリプト
ニッチ: {niche}
収益モデル: {revenue_model}

セットアップ: SETUP.md を参照してください
承認後にGitHub Actionsが自動実行します
"""
import argparse
import logging
import sys
from pathlib import Path

import yaml

# プロジェクトルートをパスに追加（empire パッケージを使えるようにする）
_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BUSINESS_DIR = _HERE


def _load_config() -> dict:
    config_path = BUSINESS_DIR / "config.yaml"
    if config_path.exists():
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {{}}
    return {{}}


def main():
    parser = argparse.ArgumentParser(description="{name}")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args()

    config = _load_config()
    logger.info(f"[{name}] 実行モード: {{args.mode}} / ニッチ: {{config.get('niche', '{niche}')}}")

    try:
        from empire.playbooks.{playbook_id} import setup, run, report
    except ImportError as e:
        logger.error(f"プレイブックのインポートに失敗: {{e}}")
        logger.error("SETUP.md の手順に従って設定を完了してから再実行してください。")
        sys.exit(1)

    setup(config, BUSINESS_DIR)

    result = run(config, BUSINESS_DIR, mode=args.mode)
    logger.info(f"処理完了: {{result}}")

    if args.mode == "weekly":
        rpt = report(config, BUSINESS_DIR)
        logger.info(f"週次レポート: {{rpt}}")


if __name__ == "__main__":
    main()
'''


# ── 事業セットアップ ──────────────────────────────────────────────────────────

def _setup_business_files(business_id: str, opportunity: dict, playbook: dict,
                          client: anthropic.Anthropic, model: str):
    """新事業のディレクトリとファイルを生成する"""
    biz_dir = BUSINESSES_DIR / business_id
    biz_dir.mkdir(parents=True, exist_ok=True)
    (biz_dir / "data").mkdir(exist_ok=True)
    (biz_dir / "logs").mkdir(exist_ok=True)

    # config.yaml
    config_content = _generate_config(opportunity, playbook, client, model)
    (biz_dir / "config.yaml").write_text(config_content, encoding="utf-8")
    logger.info(f"[Launcher] config.yaml 生成完了")

    # SETUP.md（オーナー向け案内）
    guide = _generate_setup_guide(opportunity, playbook, client, model)
    (biz_dir / "SETUP.md").write_text(guide, encoding="utf-8")

    # main.py（スケルトン）
    main_py = _generate_main_script(opportunity, playbook)
    (biz_dir / "main.py").write_text(main_py, encoding="utf-8")

    # GitHub Actions workflow
    workflow_content = _generate_github_actions(business_id, playbook)
    workflow_path = PROJECT_ROOT / ".github" / "workflows" / f"{business_id}.yml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(workflow_content, encoding="utf-8")
    logger.info(f"[Launcher] GitHub Actions: {workflow_path}")

    logger.info(f"[Launcher] 事業ファイル生成完了: {biz_dir}")


def _register_in_portfolio(business_id: str, opportunity: dict, playbook: dict):
    """portfolio.yaml に pending_human_approval で登録する"""
    portfolio = load_portfolio()
    existing_ids = [b["id"] for b in portfolio.get("businesses", [])]
    if business_id in existing_ids:
        logger.warning(f"[Launcher] {business_id} は既に登録済みです")
        return

    new_business = {
        "id": business_id,
        "type": playbook.get("id", "affiliate_seo"),
        "status": "pending_human_approval",  # 安全装置: 承認待ち
        "niche": opportunity.get("recommended_niche", ""),
        "monthly_revenue": 0,
        "monthly_cost": 0,
        "roi": 0,
        "resource_weight": 0.0,
        "started_date": str(date.today()),
        "scout_score": opportunity.get("total_score", 0),
        "scout_reason": opportunity.get("reason", ""),
        "data_dir": f"businesses/{business_id}",
    }
    portfolio.setdefault("businesses", []).append(new_business)
    portfolio.setdefault("safety", {})["new_business_last_launched"] = str(date.today())
    save_portfolio(portfolio)
    logger.info(f"[Launcher] portfolio.yaml に登録: {business_id} (pending_human_approval)")


# ── 1日1件制限チェック ────────────────────────────────────────────────────────

def _can_launch_today(portfolio: dict) -> bool:
    last = portfolio.get("safety", {}).get("new_business_last_launched", "")
    return last != str(date.today())


# ── メイン ────────────────────────────────────────────────────────────────────

def run(opportunities: list, notify_fn=None) -> str:
    """
    展開候補リストから最高スコアの機会を選んで新事業をセットアップする。
    返り値: 作成した business_id（何もしない場合は空文字）
    """
    if not opportunities:
        logger.info("[Launcher] 展開候補なし。スキップします。")
        return ""

    portfolio = load_portfolio()

    # 安全装置: 1日1件制限
    if not _can_launch_today(portfolio):
        logger.info("[Launcher] 本日は既に新事業を1件起動済みです。スキップします。")
        return ""

    # 最高スコアの機会を選択
    best = max(opportunities, key=lambda x: x.get("total_score", 0))
    logger.info(f"[Launcher] 選択された機会: {best['opportunity'][:40]} ({best['total_score']}点)")

    # プレイブック選択
    playbook = _select_playbook(best)
    logger.info(f"[Launcher] プレイブック: {playbook['name']}")

    # 事業ID生成（日付 + ニッチの先頭）
    niche_slug = best.get("recommended_niche", "new")[:8].replace(" ", "_").replace("・", "_")
    business_id = f"{date.today().strftime('%Y%m%d')}_{niche_slug}"

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        # ファイル生成
        _setup_business_files(business_id, best, playbook, client, model)

        # portfolio.yaml に登録
        _register_in_portfolio(business_id, best, playbook)

        # Slack承認リクエスト（安全装置: 起動はオーナー承認後）
        msg = (
            f"🚀 *[帝国] 新事業の準備が完了しました*\n"
            f"事業ID: `{business_id}`\n"
            f"タイプ: {playbook['name']}\n"
            f"ニッチ: {best.get('recommended_niche', '')}\n"
            f"スコア: {best['total_score']}/50点\n"
            f"理由: {best.get('reason', '')}\n\n"
            f"承認する場合:\n"
            f"  1. `businesses/{business_id}/SETUP.md` を確認\n"
            f"  2. 必要なアカウント・APIキーを設定\n"
            f"  3. GitHub Actionsで `{business_id}` ワークフローを手動実行\n\n"
            f"※ 承認しない場合は portfolio.yaml で status を 'terminated' に変更してください"
        )
        logger.info(f"[Launcher] Slack通知:\n{msg}")
        if notify_fn:
            notify_fn("帝国通知", msg)

        return business_id

    except Exception as e:
        logger.error(f"[Launcher] 事業セットアップ失敗: {e}")
        return ""
