"""
アフィリエイト × SEO記事 プレイブック
統一インターフェース: setup / run / report
"""
from pathlib import Path
from .scout import run_scout
from .creator import run_creator
from .distributor import run_distributor
from .analyzer import run_analyzer


def setup(config: dict, data_dir: Path) -> None:
    for sub in ["data/keywords", "data/articles", "logs", "public"]:
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    for f in ["data/published.jsonl", "data/keywords/history.json"]:
        p = data_dir / f
        if not p.exists():
            p.write_text("{}" if f.endswith(".json") else "", encoding="utf-8")


def run(config: dict, data_dir: Path, mode: str = "daily") -> dict:
    setup(config, data_dir)
    if mode == "daily":
        keywords = run_scout(config, data_dir)
        articles = run_creator(config, data_dir, keywords)
        result = run_distributor(config, data_dir, articles)
        return {"published": result, "mode": mode}
    insights = run_analyzer(config, data_dir)
    return {"insights": insights, "mode": mode}


def report(config: dict, data_dir: Path) -> dict:
    setup(config, data_dir)
    return run_analyzer(config, data_dir)
