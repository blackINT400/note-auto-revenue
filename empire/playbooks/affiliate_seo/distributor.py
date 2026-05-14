"""
Distributor: 生成した記事を GitHub Pages または WordPress REST API に公開する
"""
import base64
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{site_title}</title>
  <style>
    body {{ font-family: "Helvetica Neue", Arial, sans-serif; max-width: 860px;
           margin: 0 auto; padding: 1.5rem; color: #222; line-height: 1.7; }}
    h1 {{ border-bottom: 3px solid #0070f3; padding-bottom: .4rem; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ margin: .8rem 0; padding: .6rem; border-left: 4px solid #0070f3;
          background: #f5f9ff; }}
    a {{ color: #0070f3; text-decoration: none; font-weight: bold; }}
    a:hover {{ text-decoration: underline; }}
    .meta {{ font-size: .8rem; color: #888; }}
    footer {{ margin-top: 3rem; font-size: .8rem; color: #888; }}
  </style>
</head>
<body>
  <h1>{site_title}</h1>
  <ul>
{article_items}
  </ul>
  <footer>自動生成 | 更新: {updated_at}</footer>
</body>
</html>
"""


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


def _append_published(data_dir: Path, record: dict) -> None:
    path = data_dir / "data" / "published.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_index_html(
    site_title: str,
    published_records: list[dict],
    base_url: str,
) -> str:
    items = []
    for rec in reversed(published_records):
        slug = rec.get("slug", "")
        title = rec.get("title", slug)
        pub_at = rec.get("published_at", "")[:10]
        url = f"{base_url.rstrip('/')}/{slug}.html"
        items.append(
            f'    <li><a href="{url}">{title}</a>'
            f'<div class="meta">{pub_at}</div></li>'
        )
    return _INDEX_TEMPLATE.format(
        site_title=site_title,
        article_items="\n".join(items),
        updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def _publish_github_pages(
    config: dict,
    data_dir: Path,
    articles: list[dict],
) -> list[dict]:
    """public/ ディレクトリに index.html を生成し git commit する"""
    pages_dir_env = os.environ.get("GITHUB_PAGES_DIR", "")
    public_dir = Path(pages_dir_env) if pages_dir_env else data_dir / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    site_title = config.get("site_title", "アフィリエイトSEOブログ")
    base_url = config.get("base_url", "./")

    now_str = datetime.now(timezone.utc).isoformat()
    results = []

    for art in articles:
        slug = art.get("slug", "")
        html_src = Path(art.get("html_path", ""))
        html_dst = public_dir / f"{slug}.html"

        # creator がすでに public_dir へ書いている場合はそのまま利用
        if html_src != html_dst and html_src.exists():
            html_dst.write_bytes(html_src.read_bytes())

        url = f"{base_url.rstrip('/')}/{slug}.html"
        record = {
            "title": art["title"],
            "slug": slug,
            "url": url,
            "status": "published",
            "published_at": now_str,
            "keyword": art.get("keyword", ""),
            "word_count": art.get("word_count", 0),
        }
        _append_published(data_dir, record)
        results.append(record)

    # index.html を更新
    all_published = _load_published(data_dir)
    index_html = _build_index_html(site_title, all_published, base_url)
    (public_dir / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("Updated index.html with %d articles", len(all_published))

    # git commit（失敗してもクラッシュしない）
    repo_root = str(data_dir.parent.parent.parent)  # businesses/{id}/playbooks/../../../
    try:
        rel_public = str(public_dir.relative_to(Path(repo_root)))
    except ValueError:
        rel_public = str(public_dir)

    try:
        subprocess.run(
            ["git", "add", rel_public],
            cwd=repo_root, check=True, capture_output=True,
        )
        commit_msg = f"chore(affiliate_seo): publish {len(articles)} article(s)"
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=repo_root, check=True, capture_output=True,
        )
        logger.info("git commit: %s", commit_msg)
    except subprocess.CalledProcessError as exc:
        logger.warning("git operation failed (ignored): %s", exc.stderr.decode(errors="replace"))

    return results


def _publish_wordpress(
    config: dict,
    data_dir: Path,
    articles: list[dict],
) -> list[dict]:
    """WordPress REST API で記事を投稿する"""
    wp_url = os.environ.get("WORDPRESS_URL", config.get("wordpress_url", ""))
    wp_user = os.environ.get("WORDPRESS_USER", config.get("wordpress_user", ""))
    wp_password = os.environ.get("WORDPRESS_APP_PASSWORD", "")

    if not wp_url or not wp_user or not wp_password:
        logger.error("WordPress credentials not set. Need WORDPRESS_URL, WORDPRESS_USER, WORDPRESS_APP_PASSWORD")
        return []

    token = base64.b64encode(f"{wp_user}:{wp_password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }
    endpoint = f"{wp_url.rstrip('/')}/wp-json/wp/v2/posts"
    now_str = datetime.now(timezone.utc).isoformat()
    results = []

    for art in articles:
        html_path = Path(art.get("html_path", ""))
        content = html_path.read_text(encoding="utf-8") if html_path.exists() else art.get("title", "")

        payload = {
            "title": art["title"],
            "content": content,
            "status": "publish",
            "excerpt": art.get("meta_description", ""),
            "tags": art.get("tags", []),
        }
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            wp_data = resp.json()
            url = wp_data.get("link", "")
            status = "published"
            logger.info("WordPress published: %s -> %s", art["title"], url)
        except Exception as exc:
            logger.error("WordPress post failed for '%s': %s", art["title"], exc)
            url = ""
            status = "failed"

        record = {
            "title": art["title"],
            "slug": art.get("slug", ""),
            "url": url,
            "status": status,
            "published_at": now_str,
            "keyword": art.get("keyword", ""),
            "word_count": art.get("word_count", 0),
        }
        _append_published(data_dir, record)
        results.append(record)

    return results


def run_distributor(config: dict, data_dir: Path, articles: list) -> list[dict]:
    """記事を指定ターゲットに公開し結果を返す"""
    publish_target = config.get("publish_target", "github_pages")
    logger.info("Distributing %d articles to: %s", len(articles), publish_target)

    if publish_target == "wordpress":
        results = _publish_wordpress(config, data_dir, articles)
    else:
        results = _publish_github_pages(config, data_dir, articles)

    # スケールトリガー判定
    all_published = _load_published(data_dir)
    estimated_pv = len(all_published) * config.get("estimated_pv_per_article", 50)
    if estimated_pv >= 1000:
        logger.info("SCALE TRIGGER: estimated monthly PV %d >= 1000", estimated_pv)

    return results
