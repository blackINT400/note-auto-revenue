"""
agents/social.py: SNS拡散エージェント
Zenn記事公開後にThreads投稿文を生成し、Discord通知またはThreads自動投稿を行う。

- THREADS_ACCESS_TOKEN 未設定: Discord に投稿文を送信（コピペ運用）
- THREADS_ACCESS_TOKEN 設定済み: Threads API で自動投稿
"""
import logging
import os
import re
from pathlib import Path

import anthropic

from empire.utils import notify

logger = logging.getLogger(__name__)

ZENN_BASE_URL = "https://zenn.dev"
_THREADS_POST_ENDPOINT = "https://graph.threads.net/v1.0"


def _get_zenn_username() -> str:
    try:
        import yaml
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        return cfg.get("zenn_username", "") or ""
    except Exception:
        return ""


def _read_article_body(slug: str) -> str:
    """articles/{slug}.md から本文冒頭300字を取得（frontmatterを除く）"""
    path = Path("articles") / f"{slug}.md"
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    # frontmatter（---...---）を除去
    body = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL).strip()
    # Markdownの見出し記号等を簡易除去
    body = re.sub(r"^#+\s+", "", body, flags=re.MULTILINE)
    return body[:300]


def _generate_threads_text(title: str, body_preview: str, zenn_url: str) -> str:
    """Claude APIでThreads投稿文（200字以内、URL除く）を生成する"""
    client = anthropic.Anthropic()

    prompt = f"""以下のZenn記事からThreads投稿文を1つ生成してください。

記事タイトル: {title}
本文冒頭: {body_preview}
ZennのURL: {zenn_url}

制約:
- 投稿文本体は200字以内（URLは含まない）
- 冒頭は問いかけか断言で始める
- 絵文字・記号（♦◆●▶→等）は使わない
- URLは末尾に別途付けるので投稿文本体には含めない
- Threadsの読者に記事を読みたいと思わせる文章にする

投稿文本体のみを出力してください。説明や前置きは不要です。"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _post_to_threads(text: str, zenn_username: str, app_id: str, access_token: str) -> bool:
    """Threads APIに自動投稿する"""
    import requests

    user_id = app_id
    full_text = f"{text}\n{ZENN_BASE_URL}/{zenn_username}"

    # Step1: メディアコンテナ作成
    r1 = requests.post(
        f"{_THREADS_POST_ENDPOINT}/{user_id}/threads",
        params={
            "media_type": "TEXT",
            "text": full_text,
            "access_token": access_token,
        },
        timeout=15,
    )
    if r1.status_code != 200:
        logger.error("Threads コンテナ作成失敗: %s %s", r1.status_code, r1.text[:200])
        return False

    container_id = r1.json().get("id")
    if not container_id:
        logger.error("Threads コンテナIDが取得できませんでした")
        return False

    # Step2: 公開
    r2 = requests.post(
        f"{_THREADS_POST_ENDPOINT}/{user_id}/threads_publish",
        params={
            "creation_id": container_id,
            "access_token": access_token,
        },
        timeout=15,
    )
    if r2.status_code == 200:
        logger.info("Threads 自動投稿成功: %s", r2.json())
        return True
    else:
        logger.error("Threads 公開失敗: %s %s", r2.status_code, r2.text[:200])
        return False


def run(published_articles: list) -> None:
    """
    公開済み記事リストを受け取り、各記事のThreads投稿文を生成・配信する。

    Args:
        published_articles: publisher.run() の戻り値
                           [{"title": ..., "slug": ..., ...}, ...]
    """
    if not published_articles:
        return

    zenn_username = _get_zenn_username()
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    app_id = os.environ.get("THREADS_APP_ID", "")
    auto_post = bool(access_token and app_id and zenn_username)

    for article in published_articles:
        title = article.get("title", "")
        slug = article.get("slug", "")
        if not title or not slug:
            continue

        zenn_url = f"{ZENN_BASE_URL}/{zenn_username}/articles/{slug}" if zenn_username else ""
        body_preview = _read_article_body(slug)

        try:
            post_text = _generate_threads_text(title, body_preview, zenn_url)
        except Exception as e:
            logger.error("Threads投稿文生成失敗 (%s): %s", title[:30], e)
            continue

        if auto_post:
            # ── Threads自動投稿モード ────────────────────────────────────
            success = _post_to_threads(post_text, zenn_username, app_id, access_token)
            if success:
                notify(
                    "✅ Threads自動投稿完了",
                    f"**{title}**\n\n{post_text}\n\n{zenn_url}",
                )
                logger.info("Threads自動投稿完了: %s", title)
            else:
                # 失敗時はDiscordにフォールバック
                _notify_discord_manual(title, post_text, zenn_url)
        else:
            # ── Discord手動投稿モード（THREADS_ACCESS_TOKEN未設定）────────
            _notify_discord_manual(title, post_text, zenn_url)
            logger.info("Threads投稿文をDiscordに送信: %s", title)


def _notify_discord_manual(title: str, post_text: str, zenn_url: str) -> None:
    """Discordに「コピペしてThreadsに投稿してください」形式で通知する"""
    body = (
        f"タイトル: {title}\n\n"
        f"{post_text}\n\n"
        f"{zenn_url}\n\n"
        f"---\nコピペしてThreadsに投稿してください"
    )
    notify("【Threads投稿文】", body)
