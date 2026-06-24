"""
③ 生産・実行エージェント（基底クラス）
プロジェクト別に継承して専門化する
"""
from shared.claude_client import ClaudeClient
from shared.logger import get_logger

log = get_logger(__name__)


class ExecutorAgent:
    """各プロジェクトのexecutorがこのクラスを継承する"""

    def __init__(self, claude: ClaudeClient, project_config: dict):
        self.claude = claude
        self.config = project_config

    def run(self, instructions: str, materials: dict) -> dict:
        """成果物を生成して返す。サブクラスでオーバーライド推奨。"""
        log.info("実行開始")
        response = self.claude.call(
            prompt=f"指示:\n{instructions}\n\n素材:\n{materials}",
            system="あなたは高品質なコンテンツを生成するAIです。",
            model="haiku",
        )
        return {"artifacts": [response]}
