"""
Distributor: note.com 非公式 API で投稿、失敗時はマークダウン下書きとして保存する
"""
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

NOTE_API_BASE = "https://note.com/api/v1"
NOTE_API_BASE_V2 = "https://note.com/api/v2"
SESSION_PATH = NOTE_API_BASE_V2 + "/sessions"
TEXT_NOTES_PATH = NOTE_API_BASE + "/text_notes"


def _note_login(email: str, password: str) -> requests.Session | None:
    """note.com にログインしてセッションを返す。失敗時は None。"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja,en;q=0.9",
        "Origin": "https://note.com",
        "Referer": "https://note.com/login",
    })

    # まず v2 エンドポイントを試す
    for endpoint, payload in [
        (SESSION_PATH, {"login": email, "password": password}),
        (NOTE_API_BASE + "/sessions", {"login": email, "password": password}),
    ]:
        try:
            resp = session.post(endpoint, json=payload, timeout=15)
            if resp.status_code in (200, 201):
                logger.info("note.com login succeeded via %s", endpoint)
                return session
            logger.warning("note.com login attempt %s failed: %d %s", endpoint, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("note.com login error at %s: %s", endpoint, exc)

    return None


def _post_note(session: requests.Session, draft: dict, magazine_id: str = "") -> dict | None:
    """note.com に記事を投稿する。成功時はレスポンス dict を返す。"""
    title = draft.get("title_a", "無題")
    body = draft.get("body", "")
    hashtags = draft.get("hashtags", [])
    tag_str = " ".join(f"#{t}" for t in hashtags)
    full_body = f"{body}\n\n{tag_str}".strip()

    note_payload: dict = {
        "name": title,
        "body": full_body,
        "status": "public",  # 即時公開
    }
    if magazine_id:
        note_payload["magazine_id"] = magazine_id

    payload = {"note": note_payload}
    try:
        resp = session.post(TEXT_NOTES_PATH, json=payload, timeout=20)
        if resp.status_code in (200, 201):
            data = resp.json()
            note_id = data.get("data", {}).get("id") or data.get("id", "unknown")
            note_key = data.get("data", {}).get("key") or data.get("key", "")
            url = f"https://note.com/n/{note_key}" if note_key else ""
            logger.info("Posted note id=%s url=%s magazine=%s", note_id, url, magazine_id or "none")
            return {"id": note_id, "url": url}
        logger.warning("Post failed: %d %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Post error: %s", exc)
    return None


def _save_markdown(ready_dir: Path, draft: dict) -> str:
    """マークダウンとして ready/ フォルダに保存し、ファイルパスを返す。"""
    title = draft.get("title_a", "無題")
    hashtags = draft.get("hashtags", [])
    tag_str = " ".join(f"#{t}" for t in hashtags)
    body = draft.get("body", "")

    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:60]
    filename = f"{date.today().isoformat()}_{safe_title}.md"
    out_path = ready_dir / filename

    content = (
        f"# {title}\n\n"
        f"{body}\n\n"
        f"---\n\n"
        f"{tag_str}\n"
    )
    out_path.write_text(content, encoding="utf-8")
    logger.info("Saved ready markdown: %s", out_path)
    return str(out_path)


def _already_posted_today(published_path: Path) -> bool:
    """published.jsonl を確認し、今日すでに投稿済みかチェックする。"""
    if not published_path.exists():
        return False
    today = date.today().isoformat()
    with open(published_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                pub_at = rec.get("published_at", "")
                if pub_at.startswith(today):
                    return True
            except Exception:
                continue
    return False


def _append_published(published_path: Path, record: dict) -> None:
    """published.jsonl に記録を追記する。"""
    with open(published_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_distributor(config: dict, data_dir: Path, articles: list) -> list[dict]:
    """記事リストを note.com に投稿、または下書きとして保存する。"""
    post_interval_hours = config.get("post_interval_hours", 24)
    published_path = data_dir / "data" / "published.jsonl"
    ready_dir = data_dir / "data" / "drafts" / "ready"
    ready_dir.mkdir(parents=True, exist_ok=True)

    # 投稿インターバルチェック（簡易: 今日すでに投稿済みならスキップ）
    if post_interval_hours >= 24 and _already_posted_today(published_path):
        logger.info("Already posted today, skipping distributor (interval=%dh)", post_interval_hours)
        return []

    # note.com 認証情報
    email = os.environ.get("NOTE_EMAIL", "")
    password = os.environ.get("NOTE_PASSWORD", "")
    magazine_id = config.get("magazine_id", "")
    note_session = None
    if email and password:
        note_session = _note_login(email, password)

    results = []
    for article_meta in articles:
        draft_path = article_meta.get("path", "")
        if not draft_path or not Path(draft_path).exists():
            logger.warning("Draft file not found: %s", draft_path)
            continue

        with open(draft_path, encoding="utf-8") as f:
            draft = json.load(f)

        title = draft.get("title_a", "無題")
        now_iso = datetime.now(timezone.utc).isoformat()

        if note_session:
            post_result = _post_note(note_session, draft, magazine_id=magazine_id)
            if post_result:
                record = {
                    "title": title,
                    "status": "published",
                    "path_or_url": post_result.get("url", ""),
                    "published_at": now_iso,
                    "note_id": post_result.get("id", ""),
                    "draft_path": draft_path,
                }
                _append_published(published_path, record)
                results.append(record)
                continue
            # API 失敗 → フォールバック
            logger.warning("API post failed, falling back to markdown save")

        # フォールバック: マークダウン保存
        md_path = _save_markdown(ready_dir, draft)
        record = {
            "title": title,
            "status": "draft_ready",
            "path_or_url": md_path,
            "published_at": now_iso,
            "draft_path": draft_path,
        }
        _append_published(published_path, record)
        results.append(record)

    logger.info("Distributor done: %d articles processed", len(results))
    return results
