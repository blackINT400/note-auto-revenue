"""
writer.py: 記事生成エージェント
Claude APIを使ってZenn形式の記事を生成し、品質チェック・コスト管理を行う
"""
import hashlib
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import anthropic
import yaml

logger = logging.getLogger(__name__)

# Claude Sonnet 4-6 の料金（USD/百万トークン）
INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0  # 1 USD = 150 JPY（概算）

COST_TRACKER_PATH = Path("data/cost_tracker.json")
PUBLISHED_LOG_PATH = Path("logs/published.jsonl")
AB_VARIANTS_PATH = Path("data/ab_variants.json")


def _load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


# ── コスト管理 ────────────────────────────────────────────────────────────────

def _get_month_cost() -> float:
    """今月の累計APIコスト（円）"""
    if not COST_TRACKER_PATH.exists():
        return 0.0
    tracker = json.loads(COST_TRACKER_PATH.read_text(encoding="utf-8"))
    if tracker.get("month") != str(date.today())[:7]:
        return 0.0
    return tracker.get("total_jpy", 0.0)


def _record_cost(input_tokens: int, output_tokens: int) -> float:
    """APIコストを記録して今月の累計（円）を返す"""
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    jpy = usd * JPY_RATE
    current_month = str(date.today())[:7]

    COST_TRACKER_PATH.parent.mkdir(exist_ok=True)
    if COST_TRACKER_PATH.exists():
        tracker = json.loads(COST_TRACKER_PATH.read_text(encoding="utf-8"))
        if tracker.get("month") != current_month:
            tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}
    else:
        tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}

    tracker["total_jpy"] = round(tracker["total_jpy"] + jpy, 2)
    tracker["calls"].append({
        "date": str(date.today()),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_jpy": round(jpy, 2),
    })
    COST_TRACKER_PATH.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")
    return tracker["total_jpy"]


# ── 重複チェック ──────────────────────────────────────────────────────────────

def _published_hashes() -> set:
    if not PUBLISHED_LOG_PATH.exists():
        return set()
    hashes = set()
    for line in PUBLISHED_LOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if "title_hash" in entry:
                hashes.add(entry["title_hash"])
    return hashes


def _title_hash(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()[:16]


# ── Claude API 呼び出し ───────────────────────────────────────────────────────

def _call_claude(client: anthropic.Anthropic, prompt: str, model: str) -> tuple:
    """(テキスト, input_tokens, output_tokens) を返す"""
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


# ── 記事生成 ──────────────────────────────────────────────────────────────────

def _build_prompt(topic: str, niche: str, strategy: dict) -> str:
    style_hint = strategy.get("best_style", "")
    keywords = strategy.get("top_keywords", [])
    keyword_str = "、".join(keywords[:5]) if keywords else niche

    return f"""あなたはZennで月間10万PVを誇る{niche}専門ライターです。
以下の条件でZenn記事を執筆してください。

【テーマ】{topic}
【重点キーワード】{keyword_str}
【文字数】2000〜4000字
{f"【推奨文体】{style_hint}" if style_hint else ""}

【必須構成】
1. はじめに（読者の悩みへの共感・100字程度）
2. 第1章: 問題の本質を解説
3. 第2章: 具体的な解決策（数字・事例を含む）
4. 第3章: 今日から使える実践ステップ（番号付きリスト）
5. 第4章: よくある失敗と回避法
6. まとめ（行動を促すCTA付き）

【出力フォーマット】
以下のセクション区切りで厳密に出力してください（JSONではありません）:

---META_START---
TITLE_A: タイトル案A（数字・具体的情報型・30字以内）
TITLE_B: タイトル案B（問いかけ・感情訴求型・30字以内）
EMOJI: ここに絵文字1文字
TOPICS: ここにZennトピック3つをカンマ区切りで英語表記（例: money,sidejob,tax）
QUALITY_SCORE: ここに自己採点（0-100の整数）
QUALITY_REASON: ここにスコアの根拠（30字以内）
---META_END---
---BODY_START---
ここに本文マークダウン（フロントマター不要、## 見出しから始める）
---BODY_END---

【品質自己採点基準】
- 独自性・具体的な数字や事例: 40点
- 読みやすさ・論理的な構成: 30点
- 実用性・読後の行動喚起: 30点"""


def _parse_response(content: str) -> dict:
    """レスポンスからMETAとBODYを抽出する"""
    meta_match = re.search(r"---META_START---\s*(.*?)\s*---META_END---", content, re.DOTALL)
    body_match = re.search(r"---BODY_START---\s*(.*?)\s*---BODY_END---", content, re.DOTALL)

    if not meta_match or not body_match:
        raise ValueError("APIレスポンスの形式が正しくありません")

    meta_text = meta_match.group(1).strip()
    body = body_match.group(1).strip()

    def extract(key: str) -> str:
        m = re.search(rf"^{key}:\s*(.+)$", meta_text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    title_a = extract("TITLE_A")
    title_b = extract("TITLE_B")
    emoji = extract("EMOJI") or "💡"
    topics_raw = extract("TOPICS")
    quality_score_raw = extract("QUALITY_SCORE")
    quality_reason = extract("QUALITY_REASON")

    topics = [t.strip() for t in topics_raw.split(",") if t.strip()][:3]
    try:
        quality_score = int(re.sub(r"[^\d]", "", quality_score_raw))
    except (ValueError, TypeError):
        quality_score = 0

    return {
        "title": title_a,   # A案を公開タイトルとして使用
        "title_b": title_b, # B案はA/Bテスト評価用に保存
        "emoji": emoji,
        "topics": topics,
        "body": body,
        "quality_score": quality_score,
        "quality_reason": quality_reason,
    }


def _save_ab_variant(article: dict, topic: str):
    """B案タイトルをab_variants.jsonに追記（analyst.pyが後で評価する）"""
    title_b = article.get("title_b", "")
    if not title_b:
        return
    AB_VARIANTS_PATH.parent.mkdir(exist_ok=True)
    variants = []
    if AB_VARIANTS_PATH.exists():
        try:
            variants = json.loads(AB_VARIANTS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            variants = []
    variants.append({
        "date": str(date.today()),
        "topic": topic,
        "title_a": article.get("title", ""),
        "title_b": title_b,
        "quality_score": article.get("quality_score", 0),
        "evaluated": False,
    })
    AB_VARIANTS_PATH.write_text(json.dumps(variants, ensure_ascii=False, indent=2), encoding="utf-8")


def _generate_article(client: anthropic.Anthropic, topic: str, config: dict, strategy: dict) -> dict:
    """記事を生成してパースした辞書を返す（コストも記録）"""
    model = config.get("model", "claude-sonnet-4-6")
    prompt = _build_prompt(topic, config["niche"], strategy)
    content, inp, out = _call_claude(client, prompt, model)
    total_cost = _record_cost(inp, out)
    logger.info(f"API呼び出し完了（今月累計: {total_cost:.0f}円）")
    article = _parse_response(content)
    _save_ab_variant(article, topic)
    return article


# ── メイン ────────────────────────────────────────────────────────────────────

def run() -> list:
    config = _load_config()
    monthly_limit = config.get("monthly_cost_limit", 2000)
    quality_threshold = config.get("quality_threshold", 70)
    articles_per_day = config.get("articles_per_day", 2)
    strategy = config.get("auto_strategy", {})

    # ── 安全装置: コスト上限チェック ──
    current_cost = _get_month_cost()
    if current_cost >= monthly_limit:
        logger.error(f"月間コスト上限({monthly_limit}円)到達。今月の生成を停止します。")
        raise SystemExit("COST_LIMIT_EXCEEDED")

    # ── トレンドデータ読み込み ──
    trends_path = Path(f"data/trends_{date.today()}.json")
    if not trends_path.exists():
        raise FileNotFoundError(f"トレンドデータが見つかりません: {trends_path}")
    trends = json.loads(trends_path.read_text(encoding="utf-8"))

    # トピック候補リスト（はてブ → Googleトレンド → フォールバック）
    topic_candidates = []
    for item in trends.get("hatena", [])[:15]:
        topic_candidates.append(item["title"])
    topic_candidates.extend(trends.get("google_trends", [])[:10])
    if not topic_candidates:
        niche = config["niche"]
        topic_candidates = [f"{niche}で月5万円稼ぐ方法", f"{niche}の基礎から実践まで"]

    published_hashes = _published_hashes()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles = []
    topic_idx = 0

    for article_num in range(1, articles_per_day + 1):
        # コスト再チェック（記事ごとに）
        if _get_month_cost() >= monthly_limit:
            logger.warning("コスト上限に達したため記事生成を中断します")
            break

        article_data = None
        while topic_idx < len(topic_candidates):
            topic = topic_candidates[topic_idx]
            topic_idx += 1

            # 重複チェック
            if _title_hash(topic) in published_hashes:
                logger.info(f"重複スキップ: {topic[:30]}...")
                continue

            # 品質チェック付き生成（最大2回）
            for attempt in range(1, 3):
                try:
                    data = _generate_article(client, topic, config, strategy)
                    score = data.get("quality_score", 0)
                    if score >= quality_threshold:
                        data["topic"] = topic
                        data["title_hash"] = _title_hash(data["title"])
                        logger.info(f"記事{article_num} 生成成功: {data['title']} (品質: {score}点)")
                        article_data = data
                        break
                    else:
                        logger.info(f"品質スコア不足({score}点 < {quality_threshold}点) 再生成... ({attempt}/2)")
                except Exception as e:
                    logger.error(f"生成エラー: {e}")
                    break

            if article_data:
                break

        if article_data:
            articles.append(article_data)
        else:
            logger.warning(f"記事{article_num}の生成に失敗しました")

    return articles
