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


def analyze_channel(channel_config: dict) -> dict:
    channel_name = channel_config.get("name", "youtube_副業収益")
    data_dir = channel_config.get("data_dir", f"businesses/{channel_name}")

    kpi_path = Path(data_dir) / "data" / "kpi.json"
    if not kpi_path.exists():
        return {"success": False, "error": f"KPIファイルが見つかりません: {kpi_path}", "cost_jpy": 0}

    try:
        kpi = json.loads(kpi_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "error": f"KPI読み込みエラー: {e}", "cost_jpy": 0}

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        prompt = f"""あなたはYouTubeチャンネル分析の専門家です。以下のKPIデータを分析してください。

チャンネル名: {channel_name}
ニッチ: {channel_config.get('niche', '副業・節税・AI活用')}
分析日: {date.today()}

KPI:
{json.dumps(kpi, ensure_ascii=False, indent=2)}

以下のJSON形式のみで出力してください:
{{
  "analysis_date": "{date.today()}",
  "channel_health": "good",
  "key_metrics": {{"avg_ctr": 0.0, "avg_watch_time_percent": 0.0, "subscriber_growth_rate": 0.0, "revenue_per_view_jpy": 0.0}},
  "best_performing_patterns": [],
  "worst_performing_patterns": [],
  "opportunities": [],
  "risks": [],
  "recommendations": [{{"priority": 1, "action": "推奨アクション", "reason": "理由", "expected_impact": "期待効果"}}],
  "niche_pivot_needed": false,
  "niche_pivot_suggestion": "",
  "config_updates": {{}}
}}"""

        response = client.messages.create(model=model, max_tokens=2048,
            messages=[{"role": "user", "content": prompt}])

        text = response.content[0].text.strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_jpy = _calc_cost_jpy(input_tokens, output_tokens)
        record_empire_cost("youtube_analyst", input_tokens, output_tokens)

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
            result = json.loads(raw)
        except json.JSONDecodeError:
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
            result = json.loads(cleaned)

        analysis_dir = Path(data_dir) / "data"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        (analysis_dir / f"analysis_{date.today()}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        result["cost_jpy"] = round(cost_jpy, 2)
        result["success"] = True
        return result

    except Exception as e:
        logger.error(f"分析エラー: {e}")
        return {"success": False, "error": str(e), "cost_jpy": 0}
