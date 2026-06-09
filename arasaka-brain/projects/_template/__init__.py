"""
新プロジェクト追加用テンプレート
このディレクトリをコピーして projects/<name>/ として使用する
"""
from .executor import TemplateExecutor

Executor = TemplateExecutor

PROJECT_CONFIG = {
    "goal": "プロジェクトゴールをここに記述",
    "constraints": {
        "monthly_cost_limit_jpy": 300,
    },
    "research_query": "調査クエリをここに記述",
    "research_depth": "standard",
    "seo_keywords": [],
}
