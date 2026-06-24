"""
ArticleWriter — ExecutorAgent を継承した記事生成エージェント
既存の agents/writer.py ロジックをフラクタル構造に移植
"""
import json
import re
from pathlib import Path

import yaml

from agents.executor_agent import ExecutorAgent
from shared.logger import get_logger

log = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    p = PROMPTS_DIR / filename
    return p.read_text(encoding="utf-8") if p.exists() else ""


ARTICLE_SYSTEM = """
あなたは「言語化の技術 — 全1論」マガジンの専属ライターです。
以下のOSに従い、読者の感情に刺さる記事を生成してください。

{voice_os}

{human_writing_os}

## 出力フォーマット（JSON）
{{
  "title": "記事タイトル",
  "slug": "url-safe-slug",
  "quality_score": 0-100,
  "body": "記事本文（Markdown）",
  "genre": "ジャンル名",
  "tags": ["タグ1", "タグ2"]
}}
"""


class ArticleWriter(ExecutorAgent):
    def __init__(self, claude, project_config: dict):
        super().__init__(claude, project_config)
        self.voice_os = _load_prompt("HUMAN_WRITING.md")
        config_path = Path(__file__).parent / "config.yaml"
        with open(config_path) as f:
            self.pj_config = yaml.safe_load(f)

    def run(self, instructions: str, materials: dict) -> dict:
        genres = self.pj_config.get("genres", [])
        idx = self.pj_config.get("current_genre_index", 0)
        genre = genres[idx % len(genres)] if genres else {"name": "自己成長"}
        articles_per_day = self.pj_config.get("note", {}).get("articles_per_day", 2)
        quality_threshold = self.pj_config.get("kpi", {}).get("quality_threshold", 70)

        artifacts = []
        system = ARTICLE_SYSTEM.format(
            voice_os=self.voice_os,
            human_writing_os="",
        )

        for i in range(articles_per_day):
            log.info(f"記事生成 {i+1}/{articles_per_day}: ジャンル={genre['name']}")
            prompt = f"""
ジャンル: {genre['name']}
指示: {instructions}
市場データ: {materials.get('market_data', {})}
キーワード候補: {materials.get('raw_materials', [])}

このジャンルで読者の感情に刺さる記事を1本生成してください。
"""
            for attempt in range(3):
                response = self.claude.call(prompt, system=system, model="haiku")
                m = re.search(r'\{.*\}', response, re.DOTALL)
                if m:
                    try:
                        article = json.loads(m.group())
                        if article.get("quality_score", 0) >= quality_threshold:
                            artifacts.append(article)
                            log.info(f"記事生成成功: {article.get('title', '')[:40]} (score={article.get('quality_score')})")
                            break
                        else:
                            log.warning(f"品質不足 (attempt {attempt+1}): score={article.get('quality_score')}")
                    except json.JSONDecodeError:
                        log.warning(f"JSON解析失敗 (attempt {attempt+1})")
            else:
                log.error("3回試行しても品質基準未達")

            # ジャンルローテーション
            idx = (idx + 1) % len(genres)

        # ジャンルインデックス更新
        self._update_genre_index(idx)
        return {"artifacts": artifacts}

    def _update_genre_index(self, new_index: int):
        config_path = Path(__file__).parent / "config.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        cfg["current_genre_index"] = new_index
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
