"""
Creator: Claude でSEO記事を生成し Markdown / HTML として保存する
"""
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{meta_description}">
  <title>{title}</title>
  <style>
    body {{ font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", sans-serif;
           max-width: 860px; margin: 0 auto; padding: 1.5rem; line-height: 1.8; color: #222; }}
    h1 {{ font-size: 1.9rem; border-bottom: 3px solid #0070f3; padding-bottom: .4rem; }}
    h2 {{ font-size: 1.4rem; margin-top: 2.2rem; color: #0056b3; }}
    p  {{ margin: .8rem 0; }}
    a  {{ color: #0070f3; }}
    .affiliate-box {{ background: #f0f7ff; border-left: 4px solid #0070f3;
                      padding: .8rem 1rem; margin: 1rem 0; border-radius: 4px; }}
    footer {{ margin-top: 3rem; font-size: .8rem; color: #888; }}
  </style>
</head>
<body>
{body_html}
<footer>自動生成記事 | {published_at}</footer>
</body>
</html>
"""


def _romaji_slug(text: str) -> str:
    """キーワードからASCII スラグを生成する（日本語はそのままケバブ化）"""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text).strip("-").lower()
    return text[:50] or "article"


def _md_to_html(md: str) -> str:
    """最低限の Markdown → HTML 変換（h1, h2, p, a）"""
    lines = md.splitlines()
    html_lines = []
    for line in lines:
        line = line.rstrip()
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:].strip()}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:].strip()}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:].strip()}</h3>")
        elif line == "":
            html_lines.append("")
        else:
            # インラインリンク [text](url)
            line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
            # 太字
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            html_lines.append(f"<p>{line}</p>")
    return "\n".join(html_lines)


def _build_affiliate_section(affiliate_links: list[dict]) -> str:
    """アフィリエイトリンクセクションのMarkdownを生成する"""
    if not affiliate_links:
        return ""
    lines = ["\n## おすすめサービス・ツール\n"]
    for link in affiliate_links:
        name = link.get("name", "")
        url = link.get("url", "#")
        desc = link.get("description", "")
        lines.append(f"- **[{name}]({url})** — {desc}")
    return "\n".join(lines) + "\n"


def _generate_article(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    kw_dict: dict,
    affiliate_links: list[dict],
) -> dict:
    """Claude で1記事分のコンテンツを生成する"""
    keyword = kw_dict.get("keyword", niche)
    intent = kw_dict.get("search_intent", "informational")
    potential = kw_dict.get("affiliate_potential", "medium")
    affiliate_desc = "\n".join(
        f"- {a['name']}: {a.get('url','#')} ({a.get('description','')})"
        for a in affiliate_links[:5]
    ) or "なし"

    prompt = (
        f"あなたはSEOライターです。以下の条件で日本語のSEO記事を生成してください。\n\n"
        f"キーワード: {keyword}\n"
        f"ニッチ: {niche}\n"
        f"検索意図: {intent}\n"
        f"アフィリエイト商品:\n{affiliate_desc}\n\n"
        f"## 出力形式（JSONのみ、コードブロック不要）\n"
        f"{{\n"
        f'  "title": "H1タイトル（キーワード含む、40字以内）",\n'
        f'  "meta_description": "メタディスクリプション（120字以内、キーワード含む）",\n'
        f'  "tags": ["タグ1","タグ2","タグ3"],\n'
        f'  "body_md": "記事本文Markdown（3000〜4000文字）"\n'
        f"}}\n\n"
        f"## 記事構成（body_md）\n"
        f"# [キーワードを含むタイトル]\n"
        f"イントロ（200文字）: 読者の悩みに共感し、記事で学べることを提示\n"
        f"## セクション1（500〜600文字）: 背景・基礎知識\n"
        f"## セクション2（500〜600文字）: 具体的な方法・手順\n"
        f"## セクション3（500〜600文字）: 実践例・事例\n"
        f"## セクション4（500〜600文字）: 注意点・よくある失敗\n"
        f"## セクション5（500〜600文字）: 応用・発展\n"
        f"## おすすめサービス・ツール: アフィリエイトリンクを自然に紹介\n"
        f"## まとめ（200文字）: 要点整理と行動促進\n\n"
        f"アフィリエイトリンクは '[商品名](URL)' 形式で本文に自然に埋め込んでください。"
    )

    response = client.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    logger.info("Claude usage — input: %d, output: %d tokens",
                response.usage.input_tokens, response.usage.output_tokens)

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    article = json.loads(raw)
    article.setdefault("title", keyword)
    article.setdefault("meta_description", f"{keyword}について解説します。")
    article.setdefault("tags", [keyword])
    article.setdefault("body_md", f"# {keyword}\n\n準備中です。")
    return article


def run_creator(config: dict, data_dir: Path, keywords: list) -> list[dict]:
    """キーワードリストから記事を生成し保存する"""
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")
    articles_per_day = config.get("articles_per_day", 1)
    affiliate_links = config.get("affiliate_links", [])

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles_dir = data_dir / "data" / "articles"
    public_dir = data_dir / "public"
    articles_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)

    results = []
    now = datetime.now(timezone.utc)

    for kw_dict in keywords[:articles_per_day]:
        keyword = kw_dict.get("keyword", niche)
        try:
            article = _generate_article(client, model, niche, kw_dict, affiliate_links)
        except Exception as exc:
            logger.error("Article generation failed for '%s': %s", keyword, exc)
            continue

        slug = _romaji_slug(article["title"])
        body_md = article["body_md"]

        # アフィリエイトセクションが本文にない場合は末尾に追加
        if affiliate_links and "おすすめサービス" not in body_md:
            body_md += _build_affiliate_section(affiliate_links)

        # Markdown 保存
        md_path = articles_dir / f"{slug}.md"
        md_path.write_text(body_md, encoding="utf-8")

        # HTML 生成・保存
        body_html = _md_to_html(body_md)
        html_content = _HTML_TEMPLATE.format(
            title=article["title"],
            meta_description=article["meta_description"],
            body_html=body_html,
            published_at=now.strftime("%Y-%m-%d"),
        )
        html_path = public_dir / f"{slug}.html"
        html_path.write_text(html_content, encoding="utf-8")

        word_count = len(body_md.replace(" ", "").replace("\n", ""))
        result = {
            "title": article["title"],
            "slug": slug,
            "keyword": keyword,
            "meta_description": article["meta_description"],
            "tags": article.get("tags", []),
            "md_path": str(md_path),
            "html_path": str(html_path),
            "word_count": word_count,
            "created_at": now.isoformat(),
        }
        results.append(result)
        logger.info("Created article: %s (%d chars)", slug, word_count)

    return results
