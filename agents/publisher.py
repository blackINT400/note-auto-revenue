"""
publisher.py: Zenn投稿エージェント
生成済み記事をZennフォーマットのマークダウンとしてarticles/に保存し、ログを記録する
（GitHub ActionsのワークフローがGitにコミット・プッシュして自動公開される）
"""
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

ARTICLES_DIR = Path("articles")
PUBLISHED_LOG_PATH = Path("logs/published.jsonl")


def _make_slug(title: str) -> str:
    """Zennのスラグ: 日付 + タイトルのハッシュ（12文字以上の英数字・ハイフン）"""
    import hashlib
    date_str = str(date.today()).replace("-", "")
    hash_part = hashlib.sha256(title.encode()).hexdigest()[:8]
    return f"{date_str}-{hash_part}"


def _build_zenn_markdown(article: dict) -> str:
    """Zennのフロントマター付きマークダウンを生成"""
    title = article["title"].replace('"', '\\"')
    emoji = article.get("emoji", "💡")
    topics = article.get("topics", ["money", "sidejob", "tax"])
    body = article.get("body", "")

    topics_str = json.dumps(topics, ensure_ascii=False)

    return (
        f'---\n'
        f'title: "{title}"\n'
        f'emoji: "{emoji}"\n'
        f'type: "idea"\n'
        f'topics: {topics_str}\n'
        f'published: true\n'
        f'---\n\n'
        f'{body}\n'
    )


def run(articles: list) -> list:
    ARTICLES_DIR.mkdir(exist_ok=True)
    PUBLISHED_LOG_PATH.parent.mkdir(exist_ok=True)

    results = []
    for article in articles:
        title = article.get("title", "")
        if not title:
            logger.warning("タイトルが空の記事をスキップします")
            continue

        slug = _make_slug(title)
        filepath = ARTICLES_DIR / f"{slug}.md"

        content = _build_zenn_markdown(article)
        filepath.write_text(content, encoding="utf-8")
        logger.info(f"記事ファイル作成: {filepath}")

        log_entry = {
            "date": str(date.today()),
            "title": title,
            "slug": slug,
            "filename": f"{slug}.md",
            "title_hash": article.get("title_hash", ""),
            "quality_score": article.get("quality_score", 0),
            "topic": article.get("topic", ""),
            "topics": article.get("topics", []),
        }
        with PUBLISHED_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        results.append(log_entry)
        logger.info(f"投稿ログ記録: {title}")

    return results
