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


def generate_script(topic: dict, channel_config: dict) -> dict:
    channel_name = channel_config.get("name", "youtube_副業収益")
    target_audience = channel_config.get("target_audience", "20〜40代の副業・資産形成に興味がある会社員")
    video_length = channel_config.get("video_length_minutes", 8)
    data_dir = channel_config.get("data_dir", f"businesses/{channel_name}")

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        prompt = f"""あなたはYouTube台本作家のプロです。以下のトピックで完全な動画台本を作成してください。

トピック: {topic.get('title', '')}
メインキーワード: {topic.get('keyword', '')}
コンテンツアングル: {topic.get('content_angle', '')}
ターゲット視聴者: {target_audience}
動画尺: {video_length}分

構成: フック(15秒) → 本編 → CTA(30秒)

以下のJSON形式のみで出力してください:
{{
  "title": "動画タイトル",
  "hook": {{"duration_seconds": 15, "script": "フック台本", "visual_cue": "映像指示"}},
  "sections": [{{"timestamp": "00:15", "title": "セクション名", "duration_seconds": 120, "script": "台本", "visual_cue": "映像指示", "b_roll_suggestion": "挿入映像"}}],
  "cta": {{"timestamp": "07:30", "duration_seconds": 30, "script": "CTA台本"}},
  "total_word_count": 2400,
  "quality_score": 85,
  "quality_breakdown": {{"hook_strength": 90, "content_depth": 85, "cta_effectiveness": 80}},
  "quality_reason": "スコアの根拠"
}}"""

        response = client.messages.create(model=model, max_tokens=4096,
            messages=[{"role": "user", "content": prompt}])

        text = response.content[0].text.strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_jpy = _calc_cost_jpy(input_tokens, output_tokens)
        record_empire_cost("youtube_script_writer", input_tokens, output_tokens)

        # ```json ... ``` コードブロックを優先抽出
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        raw = code_block.group(1) if code_block else None
        if not raw:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                raise ValueError(f"JSON抽出失敗: {text[:200]}")
            raw = m.group()

        # 末尾カンマを除去して再パース
        try:
            script = json.loads(raw)
        except json.JSONDecodeError:
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
            script = json.loads(cleaned)

        scripts_dir = Path(data_dir) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{date.today()}_{re.sub(r'[^a-zA-Z0-9ぁ-んァ-ン一-龥]', '_', script.get('title', 'script'))[:40]}.json"
        (scripts_dir / filename).write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")

        script["cost_jpy"] = round(cost_jpy, 2)
        script["success"] = True
        return script

    except Exception as e:
        logger.error(f"台本生成エラー: {e}")
        return {"success": False, "error": str(e), "cost_jpy": 0}
