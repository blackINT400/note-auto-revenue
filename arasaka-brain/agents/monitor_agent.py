"""
⑤ 財務・KPI監視エージェント
収益追跡・コスト監視・SL管理
"""
from shared.cost_tracker import CostTracker
from shared.logger import get_logger

log = get_logger(__name__)


class MonitorAgent:
    def __init__(self, cost_tracker: CostTracker):
        self.cost_tracker = cost_tracker

    def run(self, kpi_definitions: dict, published_urls: list) -> dict:
        log.info("KPI監視実行")
        cost_summary = self.cost_tracker.monthly_summary()
        return {
            "kpi_report": {
                "published_count": len(published_urls),
                "kpi_definitions": kpi_definitions,
            },
            "revenue_data": {"status": "tracking"},
            "cost_summary": cost_summary,
            "sl_triggered": self.cost_tracker.is_sl_triggered(),
            "alert_triggered": self.cost_tracker.is_alert_triggered(),
        }
