"""
note_zenn プロジェクト — note有料マガジン + Zenn記事
ExecutorAgent を継承した ArticleWriter を Executor として公開
"""
from .article_writer import ArticleWriter

Executor = ArticleWriter

PROJECT_CONFIG = {
    "goal": "noteマガジン購読者を増やし月収¥30,000を達成する",
    "constraints": {
        "monthly_cost_limit_jpy": 500,
        "articles_per_day": 2,
        "phase": "phase0_seed",
    },
    "research_query": "恋愛・人間関係・自己成長・哲学 トレンド キーワード",
    "research_depth": "standard",
    "seo_keywords": ["言語化", "全1論", "自己成長", "人間関係", "恋愛"],
}
