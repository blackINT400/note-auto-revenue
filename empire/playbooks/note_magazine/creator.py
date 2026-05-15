"""
Creator: Claude でnote.com向け有料記事を生成し下書きとして保存する
"""
import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """タイトルからファイル名用スラグを生成する"""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:40] or "article"


def _build_prompt(niche: str, topic: dict, config: dict) -> str:
    """著者の思考OSを注入したプロンプトを構築する"""
    title_hint = topic.get("title", "")
    keywords = ", ".join(topic.get("keywords", []))
    today_genre = config.get("today_genre", niche)
    voice_os = config.get("voice_os", "")
    thought_seeds = config.get("thought_seeds", "")
    magazine_url = f"https://note.com/{config.get('note_user_id','militech_2077')}/m/{config.get('magazine_id','')}"
    author = config.get("author_pen_name", "ミリテク")

    parts = [
        f"あなたは「{author}」のゴーストライターAIです。",
        f"以下の「著者の思考OS」を完全に体現した記事を書いてください。",
        "",
    ]

    if voice_os:
        parts += [
            "## 著者の思考OS（全1論・一即全・100%再現論）",
            voice_os.strip(),
            "",
        ]

    if thought_seeds:
        parts += [
            "## 著者の思考シード（本日のインプット）",
            thought_seeds.strip(),
            "",
        ]

    parts += [
        f"## 本日のジャンル",
        f"{today_genre}",
        "",
        f"## トピック",
        f"{title_hint}",
        f"キーワード: {keywords}",
        "",
        "## 執筆ルール",
        "- 感情的な励ましを一切排する。「構造がそうなっている」という物理的事実として断定的に書く",
        "- 読者に無駄な努力を求めない。「既に持っている」ことを確信させる",
        "- 全1論の視点でジャンルを切り取り、そのジャンル特有の言葉に翻訳する",
        "- FXトレーダーとしての背景は「言語化を学んだ」という事実のみ、さらっと添える程度",
        "- 2000〜2500文字を厳守（body文字数を管理すること）",
        "",
        "## 出力形式（JSONのみ・コードブロック・前置き一切不要）",
        "{",
        '  "title_a": "全1論の視点を込めた断定的タイトル",',
        '  "title_b": "読者の悩みを直撃する疑問形タイトル",',
        '  "hashtags": ["タグ1", "タグ2", "タグ3", "タグ4", "タグ5"],',
        '  "body": "記事本文（note Markdown形式、2000〜2500文字）"',
        "}",
        "",
        "## bodyの構成",
        "1. イントロ: 読者の現実を物理的事実として描写（励まさない）",
        "2. ## 構造: [ジャンル]における「全と1」の法則",
        "3. ## 翻訳: その法則が[ジャンル]で働いている具体的な証拠",
        "4. ## 実装: 今この瞬間からできる「1つの動作」",
        "5. ## まとめ: 読者がすでに持っている能力の確認",
        "",
        f"まとめの末尾に必ず追加:\n---\nこのマガジンでは、全ジャンルに通底する「再現の法則」を毎日翻訳しています。\n→ {magazine_url}\n---",
    ]

    return "\n".join(parts)


def _generate_article(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    topic: dict,
    config: dict,
) -> dict:
    """1記事分のコンテンツを Claude で生成する"""
    prompt = _build_prompt(niche, topic, config)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
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

    if not raw:
        raise ValueError("Empty response from Claude API")

    # JSON 部分を抽出（コードブロック対応）
    # ```json ... ``` または ```...``` を除去
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    # { ... } を抽出
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)

    if not raw:
        raise ValueError("No JSON found in Claude response")

    try:
        article = json.loads(raw)
    except json.JSONDecodeError as e:
        # 末尾が切れている場合の簡易補完
        logger.warning("JSON parse failed (%s), attempting repair", e)
        # bodyが途中で切れた場合に閉じる
        repaired = raw
        if repaired.count('"') % 2 != 0:
            repaired += '"'
        # 未閉の配列
        open_brackets = repaired.count("[") - repaired.count("]")
        repaired += "]" * max(0, open_brackets)
        # 未閉のオブジェクト
        open_braces = repaired.count("{") - repaired.count("}")
        repaired += "}" * max(0, open_braces)
        article = json.loads(repaired)

    # 必須キーの保証
    title_fallback = topic.get("title", "無題")
    article.setdefault("title_a", title_fallback)
    article.setdefault("title_b", title_fallback)
    article.setdefault("hashtags", [])
    article.setdefault("body", "")
    return article


def run_creator(config: dict, data_dir: Path, topics: list) -> list[dict]:
    """トピックリストから記事を生成し下書き JSON として保存する"""
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")
    articles_per_day = config.get("articles_per_day", 1)

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    drafts_dir = data_dir / "data" / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    results = []

    for topic in topics[:articles_per_day]:
        try:
            article = _generate_article(client, model, niche, topic, config)
        except Exception as exc:
            logger.error("Article generation failed for topic '%s': %s", topic.get("title"), exc)
            continue

        slug = _slugify(article["title_a"])
        draft_path = drafts_dir / f"{today}_{slug}.json"

        draft = {
            "title_a": article["title_a"],
            "title_b": article["title_b"],
            "body": article["body"],
            "hashtags": article["hashtags"],
            "topic": topic,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)

        logger.info("Saved draft: %s", draft_path)
        results.append({
            "path": str(draft_path),
            "title_a": draft["title_a"],
            "title_b": draft["title_b"],
            "hashtags": draft["hashtags"],
            "created_at": draft["created_at"],
        })

    return results
