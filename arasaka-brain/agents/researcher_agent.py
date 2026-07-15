"""
② 調達・情報収集エージェント
リサーチ・素材収集・競合調査
"""
from shared.claude_client import ClaudeClient
from shared.logger import get_logger

log = get_logger(__name__)

SYSTEM = """
あなたはARASAKAの市場調査AIです。
与えられたクエリに対して、市場データ・競合分析・収集素材をJSONで返してください。
{"market_data": {...}, "competitor_analysis": [...], "raw_materials": [...]}
"""


class ResearcherAgent:
    def __init__(self, claude: ClaudeClient):
        self.claude = claude

    def run(self, research_query: str, depth: str = "standard") -> dict:
        log.info(f"リサーチ開始: {research_query[:50]}")
        prompt = f"調査クエリ: {research_query}\n調査深度: {depth}"
        response = self.claude.call(prompt, system=SYSTEM, model="haiku")
        import json, re
        m = re.search(r'\{.*\}', response, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {"market_data": {}, "competitor_analysis": [], "raw_materials": [response]}
