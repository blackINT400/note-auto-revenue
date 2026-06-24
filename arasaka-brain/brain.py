"""
ARASAKA BRAIN — 統括エージェント
使用法: python brain.py --project note_zenn --mode daily
"""
import argparse
import sys
from pathlib import Path

# パスを通す
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from shared.cost_tracker import CostTracker
from shared.discord_notifier import DiscordNotifier
from shared.logger import get_logger
from agents.governor_agent import GovernorAgent

log = get_logger("brain", "logs/brain.log")


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def run_project(project_name: str, mode: str):
    config = load_config()
    cost_tracker = CostTracker(config["cost"])
    notifier = DiscordNotifier(config["discord"])
    governor = GovernorAgent(config, cost_tracker, notifier)

    if cost_tracker.is_sl_triggered():
        notifier.alert("SL発動: 月次コスト上限到達。全処理停止。")
        log.error("SL発動により処理を中断")
        sys.exit(1)

    log.info(f"プロジェクト開始: {project_name} / mode={mode}")

    project_module = __import__(
        f"projects.{project_name}",
        fromlist=["run", "Executor", "PROJECT_CONFIG"],
    )

    result = governor.orchestrate(project_module, mode)

    notifier.report({
        "project": project_name,
        "mode": mode,
        "生成数": result.get("execution_count", 0),
        "コスト": f"¥{result['cost_summary']['total_jpy']} / 残り¥{result['cost_summary']['remaining_usd'] * 150:.0f}",
        "改善提案数": len(result.get("improvements", [])),
    })

    log.info(f"完了: {result}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARASAKA BRAIN 統括エージェント")
    parser.add_argument("--project", required=True, help="プロジェクト名 (例: note_zenn)")
    parser.add_argument("--mode", default="daily", help="実行モード (daily/weekly/monthly/test)")
    args = parser.parse_args()
    run_project(args.project, args.mode)
