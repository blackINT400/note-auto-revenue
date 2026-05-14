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


def _generate_article(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    topic: dict,
) -> dict:
    """1記事分のコンテンツを Claude で生成する"""
    title_hint = topic.get("title", "")
    keywords = ", ".join(topic.get("keywords", []))
    reason = topic.get("reason", "")

    prompt = (
        f"あなたはnote.comの人気有料マガジン編集者です。\n"
        f"ニッチ: {niche}\n"
        f"トピック: {title_hint}\n"
        f"キーワード: {keywords}\n"
        f"選定理由: {reason}\n\n"
        f"以下の形式でJSON を返してください。コードブロックや説明は不要です。\n\n"
        f"{{\n"
        f'  "title_a": "具体的な数字や実績を含むタイトル（例: 年収300万円サラリーマンが節税で手取り30万増やした5つの方法）",\n'
        f'  "title_b": "疑問形・感情訴求タイトル（例: あなたはまだ損してる？知らないと怖い節税の落とし穴）",\n'
        f'  "hashtags": ["ハッシュタグ1", "ハッシュタグ2", "ハッシュタグ3", "ハッシュタグ4", "ハッシュタグ5"],\n'
        f'  "body": "記事本文（2000〜3000文字、note.com Markdown形式）"\n'
        f"}}\n\n"
        f"## 記事本文の構成\n"
        f"1. キャッチーなイントロ（読者の悩みに共感）\n"
        f"2. ## セクション1: 背景・問題提起\n"
        f"3. ## セクション2: 具体的な方法1\n"
        f"4. ## セクション3: 具体的な方法2\n"
        f"5. ## セクション4: 実践のコツ・注意点\n"
        f"6. ## まとめ: 行動を促すクロージング\n\n"
        f"記事は読者が実際に行動できる具体的な内容にしてください。"
    )

    response = client.messages.create(
        model=model,
        max_tokens=3000,
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

    # JSON 部分を抽出
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    article = json.loads(raw)

    # 必須キーの保証
    article.setdefault("title_a", title_hint)
    article.setdefault("title_b", title_hint)
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
            article = _generate_article(client, model, niche, topic)
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
