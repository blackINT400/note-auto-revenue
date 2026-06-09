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


def generate_seo_package(topic: dict, script: dict, channel_config: dict) -> dict:
    niche = channel_config.get("niche", "副業・節税・AI活用")

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        sections_summary = ""
        if script.get("sections"):
            sections_summary = "\n".join(
                f"- {s.get('timestamp', '')}: {s.get('title', '')}"
                for s in script["sections"][:10]
            )

        prompt = f"""あなたはYouTube SEOの専門家です。以下の動画のSEO最適化パッケージを作成してください。

動画タイトル案: {script.get('title', topic.get('title', ''))}
メインキーワード: {topic.get('keyword', '')}
ニッチ: {niche}
動画セクション:
{sections_summary}

要件: タイトル60文字以内、説明500〜800文字、タグ15個、最適アップロード時間帯(JST)

以下のJSON形式のみで出力してください:
{{
  "title": "最終タイトル（60文字以内）",
  "title_length": 45,
  "description": "説明文",
  "description_first_150": "最初150文字",
  "tags": ["タグ1"],
  "optimal_upload_time_jst": "19:00",
  "optimal_upload_day": "土曜日",
  "upload_reason": "推奨理由",
  "chapters": [{{"timestamp": "00:00", "title": "イントロ"}}],
  "primary_keyword": "メインキーワード",
  "secondary_keywords": ["サブキーワード1"]
}}"""

        response = client.messages.create(model=model, max_tokens=2048,
            messages=[{"role": "user", "content": prompt}])

        text = response.content[0].text.strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_jpy = _calc_cost_jpy(input_tokens, output_tokens)
        record_empire_cost("youtube_seo_specialist", input_tokens, output_tokens)

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"JSON抽出失敗: {text[:200]}")
        result = json.loads(m.group())
        result["cost_jpy"] = round(cost_jpy, 2)
        result["success"] = True
        return result

    except Exception as e:
        logger.error(f"SEO生成エラー: {e}")
        return {"success": False, "error": str(e), "cost_jpy": 0}
