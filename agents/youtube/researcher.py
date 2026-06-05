"""
researcher.py: YouTubeトレンドリサーチエージェント
ビジネス/副業/節税/AI/投資ニッチのトレンドトピックを分析し、上位5件を返す
"""
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from empire.utils import get_model, record_empire_cost

logger = logging.getLogger(__name__)

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


def _calc_cost_jpy(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    return usd * JPY_RATE


def research_trends(channel_config: dict) -> dict:
    """YouTubeトレンドトピックを調査し、上位5件のトピック機会を返す"""
    niche = channel_config.get("niche", "副業・節税・AI活用")
    target_audience = channel_config.get("target_audience", "20〜40代の副業・資産形成に興味がある会社員")

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        prompt = f"""あなたはYouTubeコンテンツ戦略の専門家です。
以下のニッチにおける日本語YouTubeチャンネルのトレンドトピックを分析してください。

ニッチ: {niche}
ターゲット視聴者: {target_audience}
分析対象ジャンル: ビジネス、副業、節税、AI活用、投資・資産形成

以下の基準でトピックをスコアリングし、上位5件を選定してください:
1. 競合少なさ (0-100): 競合動画が少なく差別化できるか
2. 需要の高さ (0-100): 検索ボリュームと視聴者の関心度
3. 収益化可能性 (0-100): 広告収益・スポンサー・商品販売への転換可能性

今日の日付: {date.today()}

以下のJSON形式のみで出力してください（前置き・説明不要）:
{{
  "topics": [
    {{
      "title": "動画タイトル案（日本語・具体的）",
      "keyword": "メインキーワード",
      "estimated_monthly_searches": "月間検索数の推定（例: 5,000〜10,000）",
      "competition_score": 75,
      "demand_score": 85,
      "monetization_score": 80,
      "total_score": 80,
      "reasoning": "このトピックを選んだ理由（2〜3文）",
      "content_angle": "差別化できる切り口・アングル"
    }}
  ],
  "market_summary": "現在の市場状況の要約（3文以内）",
  "recommended_topic_index": 0
}}"""

        response = client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_jpy = _calc_cost_jpy(input_tokens, output_tokens)
        record_empire_cost("youtube_researcher", input_tokens, output_tokens)

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"JSON抽出失敗: {text[:200]}")
        result = json.loads(m.group())
        result["cost_jpy"] = round(cost_jpy, 2)
        result["success"] = True
        logger.info(f"リサーチ完了: {len(result.get('topics', []))}件のトピック取得（コスト: {cost_jpy:.1f}円）")
        return result

    except Exception as e:
        logger.error(f"リサーチエラー: {e}")
        return {"success": False, "error": str(e), "cost_jpy": 0}
