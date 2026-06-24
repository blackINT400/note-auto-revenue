"""
fx_mt4 プロジェクト — FXトレード分析・EA管理
"""
from .trade_analyzer import TradeAnalyzer

Executor = TradeAnalyzer

PROJECT_CONFIG = {
    "goal": "FXトレード分析とEA（自動売買）の性能監視",
    "constraints": {
        "monthly_cost_limit_jpy": 200,
        "analysis_frequency": "daily",
    },
    "research_query": "FX 相場分析 ドル円 ユーロ円",
    "research_depth": "standard",
    "seo_keywords": ["FX", "EA", "自動売買", "トレード分析"],
}
