"""
Analyzer: 公開記事の実績を集計し、Claude でインサイトと次のキーワードを提案する
"""
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


def _load_published(data_dir: Path) -> list[dict]:
    path = data_dir / "data" / "published.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _load_keyword_history(data_dir: Path) -> dict:
    hist_path = data_dir / "data" / "keywords" / "history.json"
    try:
        return json.loads(hist_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _this_month_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _extract_topic_clusters(records: list[dict]) -> list[str]:
    """キーワードから頻出トピッククラスターを抽出する"""
    keywords = [r.get("keyword", "") for r in records if r.get("keyword")]
    # 単語レベルで集計（2文字以上）
    words: list[str] = []
    for kw in keywords:
        parts = re.split(r"[\s・　/、,]+", kw)
        words.extend([p for p in parts if len(p) >= 2])
    counter = Counter(words)
    return [w for w, _ in counter.most_common(10)]


def _analyze_with_claude(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    records: list[dict],
    keyword_history: dict,
    top_clusters: list[str],
) -> dict:
    """Claude でインサイトと次のキーワードを生成する"""
    summaries = "\n".join(
        f"- {r.get('title','?')} (KW: {r.get('keyword','?')}, {r.get('word_count',0)}文字)"
        for r in records[-30:]
    ) or "（記事なし）"

    used_kws = list(keyword_history.keys())[-30:]
    clusters_str = ", ".join(top_clusters) or "なし"

    prompt = (
        f"あなたはSEOアナリストです。以下のデータをもとに分析してください。\n\n"
        f"ニッチ: {niche}\n"
        f"頻出トピッククラスター: {clusters_str}\n"
        f"使用済みキーワード: {', '.join(used_kws) or 'なし'}\n\n"
        f"公開済み記事（直近30件）:\n{summaries}\n\n"
        f"以下のJSON形式で返してください（コードブロック不要）:\n"
        f"{{\n"
        f'  "top_topics": ["最も成果が期待できるトピッククラスター上位3件"],\n'
        f'  "next_keywords": [\n'
        f'    {{"keyword": "次に書くべきキーワード", "reason": "理由（30字以内）"}},\n'
        f'    ... (5件)\n'
        f'  ],\n'
        f'  "recommendations": ["改善提案1", "改善提案2", "改善提案3"]\n'
        f"}}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    logger.info("Claude usage — input: %d, output: %d tokens",
                response.usage.input_tokens, response.usage.output_tokens)

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def run_analyzer(config: dict, data_dir: Path) -> dict:
    """公開記事を集計し Claude インサイトを含む分析結果を返す"""
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")
    estimated_pv_per_article = config.get("estimated_pv_per_article", 50)

    all_records = _load_published(data_dir)
    month_prefix = _this_month_prefix()

    # 今月分のみ
    this_month = [
        r for r in all_records
        if r.get("published_at", "").startswith(month_prefix)
    ]

    total_articles = len(all_records)
    articles_published = len(this_month)
    avg_word_count = (
        int(sum(r.get("word_count", 0) for r in all_records) / total_articles)
        if total_articles else 0
    )
    estimated_monthly_pv = total_articles * estimated_pv_per_article
    scale_trigger = estimated_monthly_pv >= 1000

    keyword_history = _load_keyword_history(data_dir)
    top_clusters = _extract_topic_clusters(all_records)

    # Claude インサイト
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    try:
        claude_result = _analyze_with_claude(
            client, model, niche, all_records, keyword_history, top_clusters
        )
        top_topics = claude_result.get("top_topics", top_clusters[:3])
        next_keywords = claude_result.get("next_keywords", [])
        recommendations = claude_result.get("recommendations", [])
    except Exception as exc:
        logger.error("Claude analysis failed: %s", exc)
        top_topics = top_clusters[:3]
        next_keywords = []
        recommendations = ["Claude 分析に失敗しました。API キーと設定を確認してください。"]

    result = {
        "articles_published": articles_published,
        "total_articles": total_articles,
        "avg_word_count": avg_word_count,
        "estimated_monthly_pv": estimated_monthly_pv,
        "top_topics": top_topics,
        "next_keywords": next_keywords,
        "scale_trigger": scale_trigger,
        "recommendations": recommendations,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    # 月次ファイルに保存
    out_path = data_dir / "data" / f"analysis_{month_prefix}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Analysis saved to %s (scale_trigger=%s, pv=%d)", out_path, scale_trigger, estimated_monthly_pv)

    return result
