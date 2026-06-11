"""
trend_researcher.py: YouTube BGMトレンドリサーチエージェント
BGMジャンルのトレンドを分析し、上位5件の楽曲コンセプトを返す
"""
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


def _calc_cost_jpy(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    return usd * JPY_RATE


def _get_next_vol(data_dir: str) -> int:
    """video_count.jsonから次のVol番号を返す"""
    count_path = Path(data_dir) / "video_count.json"
    try:
        if count_path.exists():
            data = json.loads(count_path.read_text())
            return data.get("count", 0) + 1
    except Exception:
        pass
    return 1


_MOCK_RESEARCH = {
    "concepts": [
        {
            "title": "Smooth Jazz & R&B – 深夜のペントハウス — 御影射すネオンとウィスキー | Vol. 1",
            "genre": "smooth jazz",
            "mood": "深夜・リラックス",
            "duration_minutes": 60,
            "competition_score": 60,
            "demand_score": 90,
            "monetization_score": 80,
            "total_score": 77,
            "reasoning": "Smooth Jazzは安定高需要。競合が少なニッチの試聴。",
            "tags": ["作業BGM", "smooth jazz", "jazz bgm", "공부할때듣는음악", "relax"]
        },
    ],
    "market_summary": "Smooth Jazzは安定高需要。",
    "recommended_index": 0,
}


def research_bgm_trends(config: dict, dry_run: bool = False) -> dict:
    """BGMトレンドを調査し上位5件のコンセプトを返す"""
    data_dir = config.get("data_dir", "youtube_bgm/data")
    next_vol = _get_next_vol(data_dir)

    if dry_run:
        logger.info("[DRY-RUN] モックリサーチデータを使用")
        mock = {**_MOCK_RESEARCH, "cost_jpy": 0.0, "success": True, "next_vol": next_vol}
        return mock

    BGM_GENRES = [
        config.get("genre_focus", "smooth jazz, R&B, relaxing"),
        "cozy indoor jazz cafe night",
        "late night smooth jazz lounge",
        "midnight chill R&B ambient",
        "luxury hotel lobby jazz",
    ]
    genre_focus = ", ".join(BGM_GENRES)
    target_use = config.get("target_use", "作業用・勉強用・睡眠用")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    prompt = f"""あなたはYouTube BGMチャンネルのコンテンツ戦略専門家です。
以下のジャンルにおけるYouTube BGM動画のトレンドを分析してください。

ジャンル: {genre_focus}
用途: {target_use}
今日の日付: {date.today()}

【タイトルフォーマット】 — 以下の形式を厳守すること：
"Smooth Jazz & R&B – [季節/時間帯] [情景の詩的な説明] | Vol. {next_vol}"

例1: "Smooth Jazz & R&B – 夏の深夜 — 雨返りのペントハウスとウィスキー | Vol. {next_vol}"
例2: "Smooth Jazz & R&B – 冬の朝 — 雪山が見える暮らしとコーヒー | Vol. {next_vol}"
例3: "Smooth Jazz & R&B – 秋の夕方 — オーシャンビューの晴れたリビングルーム | Vol. {next_vol}"

【タグのルール】
- 日本語・英語・韓国語を混在させること（検索流入を最大化するため）
- 例: ["作業BGM", "smooth jazz", "공부할때듣는음악", "jazz for study", "relaxing music"]

以下のJSON形式のみで出力（前置き不要）:
{{
  "concepts": [
    {{
      "title": "タイトル（上記フォーマットを厳守）",
      "genre": "ジャンル",
      "mood": "ムード・雰囲気",
      "duration_minutes": 60,
      "competition_score": 75,
      "demand_score": 85,
      "monetization_score": 80,
      "total_score": 80,
      "reasoning": "選定理由（2文以内）",
      "tags": ["日本語タグ", "english tag", "한국어태그", "tag4", "tag5"]
    }}
  ],
  "market_summary": "市場状況の要約3文以内）",
  "recommended_index": 0
}}"""

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    cost_jpy = _calc_cost_jpy(response.usage.input_tokens, response.usage.output_tokens)

    # JSON抽出（コードブロック → 裸JSONの順で試行）
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = code_block.group(1) if code_block else None
    if not raw:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"JSON抽出失敗: {text[:200]}")
        raw = m.group()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        result = json.loads(cleaned)

    result["cost_jpy"] = round(cost_jpy, 2)
    result["success"] = True
    result["next_vol"] = next_vol
    logger.info(f"BGMトレンドリサーチ完了: {len(result.get('concepts', []))}件 / Vol.{next_vol}")
    return result
