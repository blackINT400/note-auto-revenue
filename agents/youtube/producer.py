import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from empire.utils import get_model, notify, record_empire_cost
from agents.youtube.researcher import research_trends
from agents.youtube.script_writer import generate_script
from agents.youtube.seo_specialist import generate_seo_package
from agents.youtube.thumbnail_designer import generate_thumbnail_concepts

logger = logging.getLogger(__name__)

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


def _calc_cost_jpy(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    return usd * JPY_RATE


def _generate_weekly_calendar(topics, channel_config, client, model):
    try:
        upload_schedule = channel_config.get("upload_schedule", ["火曜日", "木曜日", "土曜日", "日曜日"])
        topics_str = "\n".join(f"{i+1}. {t.get('title', '')}（スコア: {t.get('total_score',0)}）" for i, t in enumerate(topics[:8]))
        prompt = f"""以下のトピックリストから今週の4本の動画スケジュールを作成してください。
投稿曜日: {', '.join(upload_schedule)}、今日: {date.today()}
トピック: {topics_str}

{{"week_start": "{date.today()}", "week_end": "{date.today() + timedelta(days=6)}",
  "schedule": [{{"day": "火曜日", "date": "YYYY-MM-DD", "topic_index": 0, "title": "動画タイトル", "priority": "high", "note": ""}}],
  "weekly_theme": "今週のテーマ", "content_mix": "コンテンツミックス"}}"""
        response = client.messages.create(model=model, max_tokens=1024, messages=[{"role": "user", "content": prompt}])
        record_empire_cost("youtube_producer_calendar", response.usage.input_tokens, response.usage.output_tokens)
        m = re.search(r"\{.*\}", response.content[0].text.strip(), re.DOTALL)
        return json.loads(m.group()) if m else {"error": "JSON抽出失敗"}
    except Exception as e:
        return {"error": str(e)}


def produce_video_package(channel_config: dict) -> dict:
    channel_name = channel_config.get("name", "youtube_副業収益")
    data_dir = channel_config.get("data_dir", f"businesses/{channel_name}")
    total_cost = 0.0

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = get_model()

        research_result = research_trends(channel_config)
        if not research_result.get("success"):
            return {"success": False, "error": f"リサーチ失敗: {research_result.get('error')}", "cost_jpy": total_cost}
        total_cost += research_result.get("cost_jpy", 0)

        topics = research_result.get("topics", [])
        top_topic = topics[research_result.get("recommended_topic_index", 0)] if topics else {}
        if not top_topic:
            return {"success": False, "error": "トピック取得失敗", "cost_jpy": total_cost}

        script_result = generate_script(top_topic, channel_config)
        if not script_result.get("success"):
            return {"success": False, "error": f"台本失敗: {script_result.get('error')}", "cost_jpy": total_cost}
        total_cost += script_result.get("cost_jpy", 0)

        seo_result = generate_seo_package(top_topic, script_result, channel_config)
        if not seo_result.get("success"):
            return {"success": False, "error": f"SEO失敗: {seo_result.get('error')}", "cost_jpy": total_cost}
        total_cost += seo_result.get("cost_jpy", 0)

        thumbnail_result = generate_thumbnail_concepts(top_topic, seo_result, channel_config)
        if not thumbnail_result.get("success"):
            return {"success": False, "error": f"サムネイル失敗: {thumbnail_result.get('error')}", "cost_jpy": total_cost}
        total_cost += thumbnail_result.get("cost_jpy", 0)

        calendar = _generate_weekly_calendar(topics, channel_config, client, model)

        video_package = {
            "generated_date": str(date.today()), "channel_name": channel_name,
            "topic": top_topic, "script": script_result, "seo": seo_result,
            "thumbnails": thumbnail_result, "weekly_calendar": calendar,
            "total_team_cost_jpy": round(total_cost, 2),
        }

        ready_dir = Path(data_dir) / "ready"
        ready_dir.mkdir(parents=True, exist_ok=True)
        package_path = ready_dir / f"{date.today()}_video_package.json"
        package_path.write_text(json.dumps(video_package, ensure_ascii=False, indent=2), encoding="utf-8")

        notify(
            f"YouTube動画パッケージ完成 [{channel_name}]",
            f"タイトル: {seo_result.get('title', top_topic.get('title', ''))}\n品質: {script_result.get('quality_score', 0)}点 / コスト: {total_cost:.1f}円",
        )

        return {
            "success": True, "cost_jpy": round(total_cost, 2),
            "video_package": video_package, "package_path": str(package_path),
            "title": seo_result.get("title", top_topic.get("title", "")),
            "quality_score": script_result.get("quality_score", 0),
        }

    except Exception as e:
        logger.error(f"プロデューサーエラー: {e}")
        return {"success": False, "error": str(e), "cost_jpy": round(total_cost, 2)}
