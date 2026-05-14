"""
Analyzer: 投稿履歴を分析し次週の戦略を Claude で生成する
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

SCALE_THRESHOLD = 50


def _load_published(published_path: Path) -> list[dict]:
    """published.jsonl を読み込んでリストで返す。"""
    records = []
    if not published_path.exists():
        return records
    with open(published_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def _count_drafts(data_dir: Path) -> int:
    """data/drafts 内の .json ファイル数をカウントする（ready サブフォルダは除く）。"""
    drafts_dir = data_dir / "data" / "drafts"
    if not drafts_dir.exists():
        return 0
    return len([p for p in drafts_dir.glob("*.json") if p.is_file()])


def _load_kpi(data_dir: Path) -> dict:
    """data/kpi.json を読み込む。存在しない場合は空 dict を返す。"""
    kpi_path = data_dir / "data" / "kpi.json"
    if not kpi_path.exists():
        return {}
    try:
        with open(kpi_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load kpi.json: %s", exc)
        return {}


def _analyze_with_claude(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    records: list[dict],
) -> dict:
    """Claude に投稿履歴を渡してトップトピックと推薦戦略を生成させる。"""
    if records:
        titles = "\n".join(
            f"- [{r.get('status', '?')}] {r.get('title', '無題')} ({r.get('published_at', '')[:10]})"
            for r in records[-30:]  # 直近30件
        )
        prompt = (
            f"あなたはnote.comの有料マガジン分析アドバイザーです。\n"
            f"ニッチ: {niche}\n\n"
            f"## 投稿履歴（直近）\n{titles}\n\n"
            f"以下を分析して JSON で返してください（コードブロック不要）:\n"
            f"{{\n"
            f'  "top_topics": ["最も反応が良さそうなトピック1", "トピック2", "トピック3"],\n'
            f'  "recommendations": ["来週のアクション1", "来週のアクション2", "来週のアクション3"]\n'
            f"}}"
        )
    else:
        prompt = (
            f"あなたはnote.comの有料マガジン分析アドバイザーです。\n"
            f"ニッチ: {niche}\n\n"
            f"まだ投稿がありません。このニッチで最初に書くべきトピックと戦略を JSON で返してください:\n"
            f"{{\n"
            f'  "top_topics": ["トピック1", "トピック2", "トピック3"],\n'
            f'  "recommendations": ["初期アクション1", "初期アクション2", "初期アクション3"]\n'
            f"}}"
        )

    response = client.messages.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    try:
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        logger.info(
            "Claude usage — input: %d, output: %d tokens", input_tokens, output_tokens
        )
    except Exception:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("Claude analysis JSON parse failed, using fallback")
        return {
            "top_topics": ["データ不足のため分析不可"],
            "recommendations": ["まずは5記事投稿してデータを蓄積してください"],
        }


def run_analyzer(config: dict, data_dir: Path) -> dict:
    """投稿履歴を分析し戦略レポートを返す。"""
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    published_path = data_dir / "data" / "published.jsonl"
    records = _load_published(published_path)

    articles_published = sum(1 for r in records if r.get("status") == "published")
    draft_ready_count = sum(1 for r in records if r.get("status") == "draft_ready")
    draft_count = _count_drafts(data_dir)
    total = articles_published + draft_ready_count
    publish_rate = round(articles_published / total, 3) if total > 0 else 0.0

    kpi = _load_kpi(data_dir)
    subscriber_count = kpi.get("subscribers", 0)

    # Claude 分析
    analysis = _analyze_with_claude(client, model, niche, records)
    top_topics = analysis.get("top_topics", [])
    recommendations = analysis.get("recommendations", [])

    scale_trigger = subscriber_count >= SCALE_THRESHOLD

    result = {
        "articles_published": articles_published,
        "draft_count": draft_count,
        "publish_rate": publish_rate,
        "subscriber_count": subscriber_count,
        "top_topics": top_topics,
        "recommendations": recommendations,
        "scale_trigger": scale_trigger,
    }

    # 月次ファイルに保存
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    out_path = data_dir / "data" / f"analysis_{month_key}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {**result, "generated_at": datetime.now(timezone.utc).isoformat()},
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info("Saved analysis to %s", out_path)

    if scale_trigger:
        logger.info(
            "SCALE TRIGGER: subscribers=%d >= %d", subscriber_count, SCALE_THRESHOLD
        )

    return result
