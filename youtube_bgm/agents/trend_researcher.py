"""
trend_researcher.py: YouTube BGMトレンドリサーチエージェント
BGMジャンルのトレンドを分析し、上位5件の楽曲コンセプトを返す
"""
import json
import logging
import os
import re
from datetime import date

import anthropic

logger = logging.getLogger(__name__)

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


def _calc_cost_jpy(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    return usd * JPY_RATE


def research_bgm_trends(config: dict) -> dict:
    """BGMトレンドを調査し上位5件のコンセプトを返す"""
    genre_focus = config.get("genre_focus", "lo-fi, study music, relaxing")
    target_use = config.get("target_use", "作業用・勉強用・睡眠用")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

    prompt = f"""あなたはYouTube BGMチャンネルのコンテンツ戦略専門家です。
以下のジャンルにおけるYouTube BGM動画のトレンドを分析してください。

ジャンル: {genre_focus}
用途: {target_use}
今日の日付: {date.today()}

以下の基準でトップ5コンセプトをスコアリングしてください:
1. 競合少なさ (0-100)
2. 需要の高さ (0-100)
3. 収益化可能性 (0-100)

以下のJSON形式のみで出力（前置き不要）:
{{
  "concepts": [
    {{
      "title": "動画タイトル案（日本語）",
      "genre": "ジャンル",
      "mood": "ムード・雰囲気",
      "duration_minutes": 60,
      "competition_score": 75,
      "demand_score": 85,
      "monetization_score": 80,
      "total_score": 80,
      "reasoning": "選定理由（2文以内）",
      "tags": ["tag1", "tag2", "tag3"]
    }}
  ],
  "market_summary": "市場状況の要約（3文以内）",
  "recommended_index": 0
}}"""

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    cost_jpy = _calc_cost_jpy(response.usage.input_tokens, response.usage.output_tokens)

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"JSON抽出失敗: {text[:200]}")

    result = json.loads(m.group())
    result["cost_jpy"] = round(cost_jpy, 2)
    result["success"] = True
    logger.info(f"BGMトレンドリサーチ完了: {len(result.get('concepts', []))}件")
    return result
