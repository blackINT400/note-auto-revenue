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

def _call_claude(
    client: anthropic.Anthropic,
    prompt: str,
    model: str,
    system: str = "",
) -> tuple:
    """(テキスト, input_tokens, output_tokens) を返す"""
    kwargs: dict = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


# ── 記事生成 ──────────────────────────────────────────────────────────────────

def _load_voice_os() -> str:
    """著者の思考OS（全1哲学）を読み込む"""
    voice_path = Path(__file__).parent.parent / "thoughts" / "voice_os.md"
    if voice_path.exists():
        return voice_path.read_text(encoding="utf-8")
    return ""


def _load_human_writing_os() -> str:
    """文体OS（生活の解像度が高い個人ブロガー）を読み込む"""
    path = Path(__file__).parent.parent / "thoughts" / "human_writing_os.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_thought_seeds() -> str:
    """今日の思考シード（inbox.md）を読み込む"""
    inbox_path = Path(__file__).parent.parent / "thoughts" / "inbox.md"
    if inbox_path.exists():
        content = inbox_path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return ""


def _load_owner_context() -> str:
    """owner/context_prompt.md からミリテク思考OSマスタープロンプトを読み込む"""
    context_path = Path(__file__).parent.parent / "owner" / "context_prompt.md"
    if not context_path.exists():
        return ""
    return context_path.read_text(encoding="utf-8").strip()


def _build_system_prompt() -> str:
    """systemパラメータ用: 著者OS + 文体OS + オーナーコンテキストを合成する"""
    voice_os = _load_voice_os()
    human_writing_os = _load_human_writing_os()
    owner_context = _load_owner_context()

    parts = [
        "あなたは「ミリテク」のゴーストライターAIです。",
        "以下の2つのOSを同時に体現して記事を書いてください。",
        "",
    ]
    if voice_os:
        parts += [
            "## [OS-1] 著者の思考OS（全1論・一即全・100%再現論）",
            "※ 記事の哲学的骨格。全ての論理展開はここから派生する。",
            voice_os.strip(),
            "",
        ]
    if human_writing_os:
        parts += [
            "## [OS-2] 文体OS（生活の解像度が高い個人ブロガー）",
            "※ 記事の皮膚感覚。OS-1の思想をこの文体で表現する。AI臭を完全に排除する。",
            human_writing_os.strip(),
            "",
        ]
    if owner_context:
        parts += [
            "## [OS-3] オーナーコンテキスト（思考の骨格。記事本文には一切登場させない）",
            "※ 以下の思考構造を記事の「結論・視点・構成」に反映する。",
            "※ ただし「抽象化」「具体化」「相対」「絶対」「飽和」「渇望」「全1」「0と1」",
            "※ 「翻訳」「ミリテク思考OS」などのメタ言語は記事本文に一切書かない。",
            "※ 読者には『なぜかこの記事は深い』と感じさせるが、手法は見えない状態にする。",
            owner_context,
            "",
        ]
    return "\n".join(parts)


def _build_prompt(topic: str, niche: str, strategy: dict) -> str:
    """userメッセージ用: トピック・構成・出力形式を指定する"""
    keywords = strategy.get("top_keywords", [])
    keyword_str = "、".join(keywords[:5]) if keywords else niche
    thought_seeds = _load_thought_seeds()

    seed_section = (
        f"\n## 著者の今日の思考シード（記事の起点として使う）\n{thought_seeds.strip()}\n"
        if thought_seeds else ""
    )

    return f"""以下のトピックでZenn記事を書いてください。
{seed_section}
## 執筆条件
トピック: {topic}
ジャンル: {niche}
重点キーワード: {keyword_str}
文字数: 2500〜4000字
有料パート: 全体の60%（具体的な数字・手順・体験）
無料パート: 全体の40%（読者を引き込む・問題提起）

## タイトル3案（最終的に1つ選ぶ）
以下の型でそれぞれ1案ずつ作る:
- 「なぜ〇〇は〜なのか」型（構造を暴く）
- 「〇〇万円〜〜した話」型（数字・実績訴求）
- 「〇〇をやめたら〜〜が変わった」型（逆張り体験）

## 出力フォーマット（厳守）
まず以下のJSONを1行で出力（コードブロック不要）:
{{"title": "選んだタイトル", "title_b": "没タイトル2案目", "emoji": "絵文字1字", "topics": ["zenn_topic1", "zenn_topic2", "zenn_topic3"], "score": 0-100, "reason": "スコアの根拠30字以内"}}

次の行から記事本文（Markdownのみ・フロントマター不要・## 見出しから始める）:

## 品質自己採点基準
- OS-1（全1論）が骨格として機能しているか: 40点
- OS-2（ブロガー文体）で書けているか（絵文字なし・冒頭が経験か感情・断言文体）: 30点
- 数字か固有名詞で具体性を担保できているか: 30点

## 必須CTA（本文末尾に必ず挿入）
---
この記事の「全1論」をより深く学びたい方へ → noteマガジン「言語化の技術」で毎日翻訳しています。
https://note.com/militech_2077/m/mf82e085b93c9
---"""


def _parse_response(content: str) -> dict:
    """レスポンスからJSON（1行目）と本文（2行目以降）を抽出する"""
    lines = content.strip().splitlines()

    # JSON行を探す（コードブロック除去も含む）
    json_line = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip().lstrip("`")
        if stripped.startswith("{") and stripped.endswith("}"):
            json_line = stripped
            body_start = i + 1
            break
        # ```json ブロック内の場合
        if stripped.startswith("{"):
            # 複数行JSONの可能性: 閉じ括弧まで結合
            buf = stripped
            for j in range(i + 1, len(lines)):
                buf += lines[j].strip()
                if lines[j].strip().endswith("}"):
                    json_line = buf
                    body_start = j + 1
                    break
            if json_line:
                break

    meta: dict = {}
    if json_line:
        try:
            meta = json.loads(json_line)
        except json.JSONDecodeError:
            logger.warning("JSONパース失敗、フォールバック処理へ: %s", json_line[:80])

    body = "\n".join(lines[body_start:]).strip()

    # フォールバック: ---META_START--- 形式にも対応（旧形式互換）
    if not body:
        body_match = re.search(r"---BODY_START---\s*(.*?)\s*---BODY_END---", content, re.DOTALL)
        if body_match:
            body = body_match.group(1).strip()

    title_a = meta.get("title", "")
    title_b = meta.get("title_b", "")
    emoji = meta.get("emoji", "📝")
    topics = meta.get("topics", [])[:3]
    quality_score = int(meta.get("score", 0))
    quality_reason = meta.get("reason", "")

    if not title_a:
        # タイトルが取れない場合は本文の最初の見出しから抽出
        m = re.search(r"^##?\s+(.+)$", body, re.MULTILINE)
        title_a = m.group(1).strip() if m else "無題"

    return {
        "title": title_a,
        "title_b": title_b,
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
    system_prompt = _build_system_prompt()
    user_prompt = _build_prompt(topic, config["niche"], strategy)
    content, inp, out = _call_claude(client, user_prompt, model, system=system_prompt)
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

    # 確信プロトコル用の初期化（遅延インポートでエラー耐性を確保）
    _prop_lib_ok = False
    _props_data: dict = {}
    _master_os = ""
    try:
        from empire.proposition_lib import (
            load_propositions, find_similar,
            prove_proposition, add_proposition,
            confidence_label, CONFIDENCE_EXP,
        )
        _props_data = load_propositions()
        _master_os = _load_owner_context()
        _prop_lib_ok = True
    except ImportError:
        logger.debug("[確信プロトコル] proposition_lib 未ロード。証明なしで続行。")

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

            # ── 行動確信プロトコル: 記事トピックの命題を証明 ────────────────────
            if _prop_lib_ok:
                try:
                    proposition = f"「{topic[:40]}」の記事は今週の読者に刺さる"
                    observation = f"ジャンル: {config.get('niche', '')} / 品質閾値: {quality_threshold}点"
                    similar = find_similar(proposition, _props_data)
                    proof = prove_proposition(
                        client, config.get("model", "claude-haiku-4-5-20251001"),
                        proposition, observation, _master_os, "コンテンツ", similar,
                    )
                    conf = proof.get("confidence", 0)
                    add_proposition(_props_data, proof, "コンテンツ", f"記事生成: {topic[:30]}")
                    _props_data = load_propositions()  # 最新状態に更新

                    if conf < CONFIDENCE_EXP:
                        logger.info(
                            "[確信プロトコル] 確信度不足(%d%%) — 次のトピックへ: %s",
                            conf, topic[:30],
                        )
                        continue  # 確信度50%未満は次のトピックに切り替え
                    logger.info(
                        "[確信プロトコル] 証明完了 — %s（%s）",
                        topic[:30], confidence_label(conf),
                    )
                except Exception as e:
                    logger.debug("[確信プロトコル] 証明スキップ（エラー）: %s", e)

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
