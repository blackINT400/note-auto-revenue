"""
テンプレートExecutor — ExecutorAgent を継承して専門化する
"""
from agents.executor_agent import ExecutorAgent
from shared.logger import get_logger

log = get_logger(__name__)


class TemplateExecutor(ExecutorAgent):
    def run(self, instructions: str, materials: dict) -> dict:
        log.info("テンプレートExecutor実行")
        response = self.claude.call(
            prompt=f"指示:\n{instructions}\n\n素材:\n{materials}",
            system="あなたは高品質なコンテンツを生成するAIです。",
            model="haiku",
        )
        return {"artifacts": [{"content": response}]}
