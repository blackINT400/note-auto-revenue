"""
⑦ 統治・判断エージェント
全機能統合・優先度決定・総帥への承認要求
"""
import importlib
from shared.cost_tracker import CostTracker
from shared.discord_notifier import DiscordNotifier
from shared.claude_client import ClaudeClient
from shared.logger import get_logger
from agents.architect_agent import ArchitectAgent
from agents.researcher_agent import ResearcherAgent
from agents.publisher_agent import PublisherAgent
from agents.monitor_agent import MonitorAgent
from agents.learner_agent import LearnerAgent

log = get_logger(__name__)


class GovernorAgent:
    def __init__(self, config: dict, cost_tracker: CostTracker, notifier: DiscordNotifier):
        self.config = config
        self.cost_tracker = cost_tracker
        self.notifier = notifier
        self.claude = ClaudeClient(cost_tracker)

    def orchestrate(self, project_module, mode: str) -> dict:
        log.info(f"オーケストレーション開始: mode={mode}")
        project_config = getattr(project_module, "PROJECT_CONFIG", {})

        # コストアラート確認
        if self.cost_tracker.is_alert_triggered():
            summary = self.cost_tracker.monthly_summary()
            self.notifier.alert(
                f"コスト警告: {summary['usage_pct']}% 使用済み "
                f"(¥{summary['total_jpy']} / 残り ¥{summary['total_jpy']})"
            )

        # ① 戦略
        architect = ArchitectAgent(self.claude)
        strategy = architect.run(
            project_goal=project_config.get("goal", "収益化"),
            constraints=project_config.get("constraints", {}),
        )

        # ② リサーチ
        researcher = ResearcherAgent(self.claude)
        research = researcher.run(
            research_query=project_config.get("research_query", "市場動向"),
            depth=project_config.get("research_depth", "standard"),
        )

        # ③ 実行（プロジェクト固有）
        executor_cls = getattr(project_module, "Executor", None)
        if executor_cls:
            executor = executor_cls(self.claude, project_config)
            execution = executor.run(
                instructions=str(strategy.get("roadmap", [])),
                materials=research,
            )
        else:
            execution = {"artifacts": []}

        # ④ 配布
        publisher = PublisherAgent(self.claude)
        distribution = publisher.run(
            artifacts=execution.get("artifacts", []),
            seo_keywords=project_config.get("seo_keywords", []),
        )

        # ⑤ KPI監視
        monitor = MonitorAgent(self.cost_tracker)
        monitoring = monitor.run(
            kpi_definitions=strategy.get("kpi_definitions", {}),
            published_urls=distribution.get("published_urls", []),
        )

        # ⑥ 学習
        learner = LearnerAgent(self.claude)
        learning = learner.run(
            kpi_report=monitoring["kpi_report"],
            artifacts=execution.get("artifacts", []),
        )

        result = {
            "mode": mode,
            "strategy": strategy,
            "execution_count": len(execution.get("artifacts", [])),
            "distribution": distribution["distribution_report"],
            "cost_summary": monitoring["cost_summary"],
            "improvements": learning.get("improvement_suggestions", []),
        }

        # SL確認
        if monitoring.get("sl_triggered"):
            self.notifier.alert("SL発動: 月次コスト上限到達。全処理停止。")
            result["sl_triggered"] = True

        log.info(f"オーケストレーション完了: {result['execution_count']}件生成")
        return result
