"""
簡易APIサービス（RapidAPI）プレイブック
統一インターフェース: setup / run / report
"""
from pathlib import Path
from .scout import run_scout
from .creator import run_creator
from .distributor import run_distributor
from .analyzer import run_analyzer


def setup(config: dict, data_dir: Path) -> None:
    for sub in ["app", "data", "logs", "data/requests"]:
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    for f in ["data/metrics.jsonl", "data/services.jsonl"]:
        p = data_dir / f
        if not p.exists():
            p.touch()


def run(config: dict, data_dir: Path, mode: str = "daily") -> dict:
    setup(config, data_dir)
    if mode == "daily":
        insights = run_analyzer(config, data_dir)
        return {"metrics": insights, "mode": mode}
    # weekly: scout new opportunities + maybe create new service version
    opportunities = run_scout(config, data_dir)
    services = run_creator(config, data_dir, opportunities)
    result = run_distributor(config, data_dir, services)
    return {"deployed": result, "mode": mode}


def report(config: dict, data_dir: Path) -> dict:
    setup(config, data_dir)
    return run_analyzer(config, data_dir)
