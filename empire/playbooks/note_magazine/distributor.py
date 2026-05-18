"""
Distributor: note.com 非公式 API で投稿、失敗時はマークダウン下書きとして保存し、
             メール通知でオーナーが手動投稿できるようにする
"""
import json
import logging
import os
import smtplib
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

NOTE_API_BASE = "https://note.com/api/v1"
TEXT_NOTES_PATH = NOTE_API_BASE + "/text_notes"


def _note_session() -> requests.Session | None:
    """
    _note_session_v5 Cookie を使ってセッションを返す。
    トップページにアクセスしてCSRFトークンも取得する。
    """
    cookie_value = os.environ.get("NOTE_SESSION_COOKIE", "")
    if not cookie_value:
        logger.warning("NOTE_SESSION_COOKIE が未設定です。自動投稿をスキップします。")
        return None

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja,en;q=0.9",
        "Origin": "https://note.com",
        "Referer": "https://note.com/",
    })
    session.cookies.set("_note_session_v5", cookie_value, domain="note.com")

    # CSRFトークンをトップページから取得
    try:
        resp = session.get("https://note.com/", timeout=15)
        # meta[name="csrf-token"] または X-CSRF-Token を探す
        import re as _re
        csrf_match = _re.search(r'<meta name="csrf-token" content="([^"]+)"', resp.text)
        if csrf_match:
            csrf_token = csrf_match.group(1)
            session.headers.update({"X-CSRF-Token": csrf_token})
            logger.info("note.com: CSRFトークン取得成功")
        else:
            logger.warning("note.com: CSRFトークンが見つかりません（投稿可能な場合もあります）")
    except Exception as exc:
        logger.warning("note.com: トップページ取得エラー: %s", exc)

    logger.info("note.com: Cookie認証セッションを初期化しました")
    return session


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
        resp = session.post(
            TEXT_NOTES_PATH,
            json=payload,
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            note_id = data.get("data", {}).get("id") or data.get("id", "unknown")
            note_key = data.get("data", {}).get("key") or data.get("key", "")
            url = f"https://note.com/n/{note_key}" if note_key else ""
            logger.info("Posted note id=%s url=%s magazine=%s", note_id, url, magazine_id or "none")
            return {"id": note_id, "url": url}
        logger.warning("Post failed: %d %s", resp.status_code, resp.text[:500])
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
    """
    published.jsonl を確認し、今日すでに note.com への投稿が成功済みかチェックする。
    draft_ready（マークダウン保存のみ）は投稿済みとみなさない。
    """
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
                status = rec.get("status", "")
                # draft_ready は「投稿失敗してローカル保存した」状態なので除外
                if pub_at.startswith(today) and status == "published":
                    return True
            except Exception:
                continue
    return False


def _append_published(published_path: Path, record: dict) -> None:
    """published.jsonl に記録を追記する。"""
    with open(published_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _send_email_notification(draft: dict, md_path: str) -> bool:
    """
    記事をメール本文として送信する（Gmail SMTP使用）。
    オーナーがそのままnote.comにコピペできる形式。
    環境変数:
      GMAIL_ADDRESS   : 送信者のGmailアドレス
      GMAIL_APP_PASSWORD: Gmailのアプリパスワード（2段階認証必須）
      NOTIFY_EMAIL    : 通知先メールアドレス（未設定ならGMAIL_ADDRESSと同じ）
    """
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    notify_email = os.environ.get("NOTIFY_EMAIL", "") or gmail_address

    if not gmail_address or not app_password:
        logger.warning("GMAIL_ADDRESS / GMAIL_APP_PASSWORD が未設定。メール通知をスキップします。")
        return False

    title = draft.get("title_a", "無題")
    body = draft.get("body", "")
    hashtags = draft.get("hashtags", [])
    tag_str = " ".join(f"#{t}" for t in hashtags)
    today = date.today().isoformat()

    subject = f"【note記事】{today}: {title}"

    # メール本文（note.comにコピペできる形式）
    email_body = f"""note.com に投稿する記事が準備できました。
以下をnote.comのエディタにコピー＆ペーストしてください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
タイトル: {title}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{body}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ハッシュタグ:
{tag_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

保存先ファイル: {md_path}

このメールは自動送信されました。
"""

    try:
        msg = MIMEMultipart()
        msg["From"] = gmail_address
        msg["To"] = notify_email
        msg["Subject"] = subject
        msg.attach(MIMEText(email_body, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, app_password)
            server.sendmail(gmail_address, notify_email, msg.as_string())

        logger.info("メール通知送信完了: %s → %s", subject[:50], notify_email)
        return True
    except Exception as exc:
        logger.warning("メール送信エラー: %s", exc)
        return False


def _send_discord_article(draft: dict, abstraction_meta: dict) -> None:
    """STEP4: 生成記事 + 市場分析メタ情報を Discord に送信する"""
    import os as _os
    webhook_url = _os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL 未設定 — Discord記事送信スキップ")
        return

    title = draft.get("title_a", "無題")
    body = draft.get("body", "")
    hashtags = draft.get("hashtags", [])
    tag_str = " ".join(f"#{t}" for t in hashtags)

    ref_title = abstraction_meta.get("reference_title", "（取得中）")
    abstract_structure = abstraction_meta.get("abstract_structure", "（取得中）")
    today_genre = draft.get("topic", {}).get("title", "（今日のジャンル）")
    replicable_pattern = abstraction_meta.get("replicable_pattern", "")

    meta_header = (
        f"参考にした人気記事: {ref_title}\n"
        f"抽象化した構造: {abstract_structure}\n"
        f"翻訳したテーマ: {today_genre}"
    )
    if replicable_pattern:
        meta_header += f"\n翻訳パターン: {replicable_pattern}"

    # Discord の embed description は 4096 文字まで
    body_preview = body[:1800] + "…（続き）" if len(body) > 1800 else body
    full_text = f"タイトル: {title}\n\n{body_preview}\n\n---\n{tag_str}"

    embeds = [
        {
            "title": "📝 市場分析レポート",
            "description": meta_header[:4096],
            "color": 0xF59E0B,
        },
        {
            "title": f"【note投稿用記事】{title}",
            "description": full_text[:4096],
            "color": 0x1D9E75,
        },
    ]

    try:
        resp = requests.post(
            webhook_url,
            json={"embeds": embeds},
            timeout=15,
        )
        if resp.status_code in (200, 204):
            logger.info("Discord記事送信完了: %s", title[:50])
        else:
            logger.warning("Discord記事送信失敗: %d %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Discord記事送信エラー: %s", exc)


def run_distributor(config: dict, data_dir: Path, articles: list, abstraction_meta: dict | None = None) -> list[dict]:
    """記事リストを note.com に投稿、または下書きとして保存する。"""
    post_interval_hours = config.get("post_interval_hours", 24)
    published_path = data_dir / "data" / "published.jsonl"
    ready_dir = data_dir / "data" / "drafts" / "ready"
    ready_dir.mkdir(parents=True, exist_ok=True)

    # 投稿インターバルチェック（簡易: 今日すでに投稿済みならスキップ）
    if post_interval_hours >= 24 and _already_posted_today(published_path):
        logger.info("Already posted today, skipping distributor (interval=%dh)", post_interval_hours)
        return []

    # note.com Cookie認証
    magazine_id = config.get("magazine_id", "")
    note_session = _note_session()

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
                # STEP4: Discord に記事 + 市場分析メタを送信
                _send_discord_article(draft, abstraction_meta or {})
                continue
            logger.warning("API post failed, falling back to markdown save")

        # フォールバック: マークダウン保存 + メール通知
        md_path = _save_markdown(ready_dir, draft)
        _send_email_notification(draft, md_path)
        record = {
            "title": title,
            "status": "draft_ready",
            "path_or_url": md_path,
            "published_at": now_iso,
            "draft_path": draft_path,
        }
        _append_published(published_path, record)
        results.append(record)
        # STEP4: Discord に記事 + 市場分析メタを送信
        _send_discord_article(draft, abstraction_meta or {})

    logger.info("Distributor done: %d articles processed", len(results))
    return results
