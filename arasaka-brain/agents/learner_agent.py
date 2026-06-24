"""
⑥ 学習・改善エージェント
結果分析・プロンプト進化・ブラッシュアップ
"""
from shared.claude_client import ClaudeClient
from shared.logger import get_logger

log = get_logger(__name__)

SYSTEM = """
あなたはARASAKAの改善分析AIです。
KPIレポートと成果物を分析し、改善提案をJSON形式で返してください。
{"improvement_suggestions": [...], "prompt_updates": {...}, "priority": "high|medium|low"}
"""


class LearnerAgent:
    def __init__(self, claude: ClaudeClient):
        self.claude = claude

    def run(self, kpi_report: dict, artifacts: list) -> dict:
        log.info("学習・改善分析開始")
        prompt = f"KPIレポート:\n{kpi_report}\n\n成果物サンプル数: {len(artifacts)}"
        response = self.claude.call(prompt, system=SYSTEM, model="haiku")
        import json, re
        m = re.search(r'\{.*\}', response, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {"improvement_suggestions": [response], "prompt_updates": {}, "priority": "medium"}
