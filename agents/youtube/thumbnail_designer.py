"""
thumbnail_designer.py: サムネイルコンセプト生成エージェント
CTR最大化のためのサムネイルコンセプト3案を生成する
"""
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
    """サムネイルコンセプト3案とMidjourney/DALL-Eプロンプトを生成する"""
    niche = channel_config.get("niche", "副業・節税・AI活用")
    brand_color = channel_config.get("brand_color", "#FF4444")

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        prompt = f"""あなたはYouTubeサムネイルデザインの専門家です。
CTRを最大化するサムネイルコンセプトを3案作成してください。

動画タイトル: {seo_package.get('title', topic.get('title', ''))}
ニッチ: {niche}
メインキーワード: {topic.get('keyword', '')}
ブランドカラー: {brand_color}

各コンセプトには以下を含めてください:
- テキストオーバーレイ（サムネイル上の文字）
- 画像構成（人物・背景・アイコン等の配置）
- カラースキーム
- CTRポテンシャルスコア（0-100）
- ブランド一貫性スコア（0-100）
- 好奇心ギャップスコア（0-100）（見た人が続きを見たくなるか）
- Midjourney/DALL-Eプロンプト（英語）
- A/Bテスト優先度（1=最優先）

以下のJSON形式のみで出力してください:
{{
  "concepts": [
    {{
      "concept_id": 1,
      "text_overlay": "サムネイル上のテキスト（短く・インパクト重視）",
      "image_composition": "画像構成の説明（人物位置・背景・アイコン等）",
      "color_scheme": "使用カラーの説明",
      "ctr_score": 85,
      "brand_consistency_score": 80,
      "curiosity_gap_score": 90,
      "total_score": 85,
      "midjourney_prompt": "YouTube thumbnail, [description in English], professional, high contrast, --ar 16:9",
      "dalle_prompt": "YouTube thumbnail design: [description in English]",
      "ab_test_priority": 1,
      "priority_reason": "このコンセプトを最優先にする理由"
    }}
  ],
  "recommended_concept_id": 1,
  "ab_test_strategy": "A/Bテストの進め方の提案"
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
        record_empire_cost("youtube_thumbnail_designer", input_tokens, output_tokens)

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"JSON抽出失敗: {text[:200]}")
        result = json.loads(m.group())
        result["cost_jpy"] = round(cost_jpy, 2)
        result["success"] = True
        logger.info(f"サムネイルコンセプト生成完了（コスト: {cost_jpy:.1f}円）")
        return result

    except Exception as e:
        logger.error(f"サムネイル生成エラー: {e}")
        return {"success": False, "error": str(e), "cost_jpy": 0}
