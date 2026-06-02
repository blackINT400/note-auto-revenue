"""
note有料マガジン プレイブック
統一インターフェース: setup / run / report
"""
from pathlib import Path
from .scout import run_scout
from .creator import run_creator
from .distributor import run_distributor
from .analyzer import run_analyzer
from .pattern_analyzer import run_pattern_analyzer


def setup(config: dict, data_dir: Path) -> None:
    """ディレクトリ構造を初期化する"""
    for sub in ["data/drafts", "data/drafts/ready", "logs"]:
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    published = data_dir / "data" / "published.jsonl"
    if not published.exists():
        published.touch()


def run(config: dict, data_dir: Path, mode: str = "daily") -> dict:
    """
    daily : scout(STEP1+2) → creator(STEP3) → distributor(STEP4: Discord)
    weekly: pattern_analyzer(note人気記事収集・分析) → analyzer(戦略更新)
    """
    setup(config, data_dir)
    if mode == "daily":
        topics, abstraction_meta = run_scout(config, data_dir)
        articles = run_creator(config, data_dir, topics, abstraction_meta)
        result = run_distributor(config, data_dir, articles, abstraction_meta)
        return {"published": result, "mode": mode, "abstraction": abstraction_meta}

    # weekly: パターン分析 → 戦略更新
    patterns = run_pattern_analyzer(config, data_dir)
    insights = run_analyzer(config, data_dir)
    return {"patterns": patterns, "insights": insights, "mode": mode}


def report(config: dict, data_dir: Path) -> dict:
    """パフォーマンスレポートを返す"""
    setup(config, data_dir)
    return run_analyzer(config, data_dir)
