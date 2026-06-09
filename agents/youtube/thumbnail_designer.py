import json
import logging
import os
import re
import sys
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


def generate_thumbnail_concepts(topic: dict, seo_package: dict, channel_config: dict) -> dict:
    niche = channel_config.get("niche", "副業・節税・AI活用")
    brand_color = channel_config.get("brand_color", "#FF4444")

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        prompt = f"""あなたはYouTubeサムネイルデザインの専門家です。CTRを最大化するコンセプト3案を作成してください。

動画タイトル: {seo_package.get('title', topic.get('title', ''))}
ニッチ: {niche}
メインキーワード: {topic.get('keyword', '')}
ブランドカラー: {brand_color}

以下のJSON形式のみで出力してください:
{{
  "concepts": [
    {{
      "concept_id": 1,
      "text_overlay": "サムネイル上のテキスト",
      "image_composition": "画像構成",
      "color_scheme": "カラー説明",
      "ctr_score": 85,
      "brand_consistency_score": 80,
      "curiosity_gap_score": 90,
      "total_score": 85,
      "midjourney_prompt": "YouTube thumbnail, --ar 16:9",
      "dalle_prompt": "YouTube thumbnail design",
      "ab_test_priority": 1,
      "priority_reason": "優先理由"
    }}
  ],
  "recommended_concept_id": 1,
  "ab_test_strategy": "A/Bテスト戦略"
}}"""

        response = client.messages.create(model=model, max_tokens=2048,
            messages=[{"role": "user", "content": prompt}])

        text = response.content[0].text.strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_jpy = _calc_cost_jpy(input_tokens, output_tokens)
        record_empire_cost("youtube_thumbnail_designer", input_tokens, output_tokens)

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"JSON抽出失敗: {text[:200]}")
        result = json.loads(m.group())
        result["cost_jpy"] = round(cost_jpy, 2)
        result["success"] = True
        return result

    except Exception as e:
        logger.error(f"サムネイル生成エラー: {e}")
        return {"success": False, "error": str(e), "cost_jpy": 0}
