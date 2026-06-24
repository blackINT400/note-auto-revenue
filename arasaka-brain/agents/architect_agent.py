"""
① 戦略・設計エージェント
ゴール定義・構造設計・ロードマップ生成
"""
from shared.claude_client import ClaudeClient
from shared.logger import get_logger

log = get_logger(__name__)

SYSTEM = """
あなたはARASAKA DIGITAL HOLDINGSの戦略立案AIです。
与えられたプロジェクトゴールと制約から、実行可能なロードマップとKPI・SL条件を設計してください。
JSONフォーマットで出力: {"roadmap": [...], "kpi_definitions": {...}, "sl_conditions": {...}}
"""


class ArchitectAgent:
    def __init__(self, claude: ClaudeClient):
        self.claude = claude

    def run(self, project_goal: str, constraints: dict) -> dict:
        log.info(f"戦略設計開始: {project_goal[:50]}")
        prompt = f"""
プロジェクトゴール: {project_goal}
制約条件: {constraints}

ロードマップ・KPI・SL条件を設計してください。
"""
        response = self.claude.call(prompt, system=SYSTEM, model="haiku")
        import json, re
        m = re.search(r'\{.*\}', response, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {"roadmap": [response], "kpi_definitions": {}, "sl_conditions": {}}
