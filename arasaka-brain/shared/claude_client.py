"""
Anthropic API ラッパー
- デフォルト: Haiku 4.5（コスト最小）
- Batch API: 50%オフ、即時性不要タスクに使用
- コストを自動記録
"""
import anthropic
from .cost_tracker import CostTracker, PRICING


class ClaudeClient:
    MODELS = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
    }

    def __init__(self, cost_tracker: CostTracker):
        self.client = anthropic.Anthropic()
        self.cost_tracker = cost_tracker

    def call(self, prompt: str, system: str = "", model: str = "haiku") -> str:
        model_id = self.MODELS.get(model, model)
        response = self.client.messages.create(
            model=model_id,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        usage = response.usage
        self.cost_tracker.record(model_id, usage.input_tokens, usage.output_tokens)
        return response.content[0].text

    def batch_call(self, requests: list) -> list:
        """Batch API — 50%オフ、24時間以内処理"""
        batch_requests = []
        for i, req in enumerate(requests):
            batch_requests.append({
                "custom_id": req.get("id", f"req_{i}"),
                "params": {
                    "model": self.MODELS.get(req.get("model", "haiku"), req.get("model", "haiku")),
                    "max_tokens": req.get("max_tokens", 2000),
                    "system": req.get("system", ""),
                    "messages": [{"role": "user", "content": req["prompt"]}],
                }
            })
        batch = self.client.messages.batches.create(requests=batch_requests)
        return {"batch_id": batch.id, "status": batch.processing_status}
