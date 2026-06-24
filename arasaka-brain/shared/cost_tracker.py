"""
月次コスト追跡・SL管理
1USD = 150JPY で換算
"""
import json
from pathlib import Path
from datetime import datetime


PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-sonnet-4-6":         {"input": 3.0, "output": 15.0},
}


class CostTracker:
    USD_TO_JPY = 150

    def __init__(self, cost_config: dict):
        self.limit_usd = cost_config["monthly_limit_usd"]
        self.alert_threshold = cost_config["alert_threshold"]
        self.sl_threshold = cost_config["sl_threshold"]
        self.log_path = Path(__file__).parent.parent / ".cost_log.json"
        self._load()

    def record(self, model: str, input_tokens: int, output_tokens: int):
        pricing = PRICING.get(model, {"input": 3.0, "output": 15.0})
        cost_usd = (
            input_tokens  / 1_000_000 * pricing["input"] +
            output_tokens / 1_000_000 * pricing["output"]
        )
        self.data["total_usd"] += cost_usd
        self._save()

    def is_sl_triggered(self) -> bool:
        return self.data["total_usd"] >= self.limit_usd * self.sl_threshold

    def is_alert_triggered(self) -> bool:
        return self.data["total_usd"] >= self.limit_usd * self.alert_threshold

    def monthly_summary(self) -> dict:
        total_usd = self.data["total_usd"]
        return {
            "total_usd": round(total_usd, 4),
            "total_jpy": round(total_usd * self.USD_TO_JPY),
            "remaining_usd": round(self.limit_usd - total_usd, 4),
            "usage_pct": round(total_usd / self.limit_usd * 100, 1),
        }

    def _load(self):
        month = datetime.now().strftime("%Y-%m")
        if self.log_path.exists():
            with open(self.log_path) as f:
                data = json.load(f)
            if data.get("month") != month:
                data = {"month": month, "total_usd": 0.0}
        else:
            data = {"month": month, "total_usd": 0.0}
        self.data = data

    def _save(self):
        with open(self.log_path, "w") as f:
            json.dump(self.data, f)
