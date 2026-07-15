"""
TradeAnalyzer — ExecutorAgent を継承したFX分析エージェント
"""
from agents.executor_agent import ExecutorAgent
from shared.logger import get_logger

log = get_logger(__name__)

SYSTEM = """
あなたはFXトレード分析AIです。
市場データを分析し、トレード戦略とEA改善提案をJSON形式で返してください。
{"analysis": {...}, "trade_signals": [...], "ea_improvements": [...]}
"""


class TradeAnalyzer(ExecutorAgent):
    def run(self, instructions: str, materials: dict) -> dict:
        log.info("FX分析開始")
        prompt = f"市場データ: {materials}\n指示: {instructions}\nトレード分析を実施してください。"
        response = self.claude.call(prompt, system=SYSTEM, model="haiku")
        return {"artifacts": [{"type": "fx_analysis", "content": response}]}
