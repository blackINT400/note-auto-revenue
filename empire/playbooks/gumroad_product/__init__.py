"""
Gumroad デジタル商品販売 プレイブック
統一インターフェース: setup / run / report
"""
from pathlib import Path
from .scout import run_scout
from .creator import run_creator
from .distributor import run_distributor
from .analyzer import run_analyzer


def setup(config: dict, data_dir: Path) -> None:
    for sub in ["data/products", "data/content", "data/products/pending", "logs"]:
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    for f in ["data/products.jsonl"]:
        p = data_dir / f
        if not p.exists():
            p.touch()


def run(config: dict, data_dir: Path, mode: str = "daily") -> dict:
    setup(config, data_dir)
    if mode == "daily":
        opportunities = run_scout(config, data_dir)
        products = run_creator(config, data_dir, opportunities)
        result = run_distributor(config, data_dir, products)
        return {"listed": result, "mode": mode}
    insights = run_analyzer(config, data_dir)
    return {"insights": insights, "mode": mode}


def report(config: dict, data_dir: Path) -> dict:
    setup(config, data_dir)
    return run_analyzer(config, data_dir)
