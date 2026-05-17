"""
businesses/affiliate_言語化SEO/main.py
アフィリエイト × SEO記事 自動生成エージェント

--mode daily  : 記事を1〜2本生成して articles/ に保存
--mode weekly : パフォーマンス集計 + キーワード戦略更新
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic
import yaml

# ── パス設定 ──────────────────────────────────────────────────────────────────
BIZ_DIR     = Path(__file__).parent
ARTICLES_DIR = BIZ_DIR / "articles"
DATA_DIR    = BIZ_DIR / "data"
LOG_DIR     = BIZ_DIR / "logs"
CONFIG_PATH = BIZ_DIR / "config.yaml"
PUB_LOG     = DATA_DIR / "published.jsonl"
STRATEGY_PATH = DATA_DIR / "strategy.json"

for d in (ARTICLES_DIR, DATA_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── ログ ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"main_{date.today()}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── Discord 通知 ───────────────────────────────────────────────────────────────
def _notify(title: str, body: str, urgent: bool = False) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    import requests
    color = 0xFF4444 if urgent else 0x1D9E75
    try:
        requests.post(url, json={"embeds": [{"title": title[:256],
            "description": body[:4000], "color": color}]}, timeout=10)
    except Exception:
        pass


# ── 設定読み込み ────────────────────────────────────────────────────────────────
def _load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _load_strategy() -> dict:
    default = {
        "keywords": ["言語化", "自己成長", "恋愛哲学", "人間関係", "思考OS"],
        "last_updated": "",
    }
    if not STRATEGY_PATH.exists():
        return default
    try:
        return json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_strategy(data: dict) -> None:
    STRATEGY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_published() -> list:
    if not PUB_LOG.exists():
        return []
    records = []
    for line in PUB_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _append_published(record: dict) -> None:
    with PUB_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── アフィリエイトリンクブロック生成 ──────────────────────────────────────────
def _affiliate_block(links: list, topic: str) -> str:
    relevant = [l for l in links if l.get("url")]
    if not relevant:
        return ""
    lines = ["\n## おすすめサービス・書籍\n"]
    for l in relevant[:3]:
        lines.append(f"- [{l['name']}]({l['url']})")
    return "\n".join(lines) + "\n"


# ── 記事生成 ────────────────────────────────────────────────────────────────────
def _generate_article(
    client: anthropic.Anthropic,
    model: str,
    keyword: str,
    niche: str,
    affiliate_block: str,
    author: str,
) -> dict | None:
    """Claude で SEO記事を生成して dict を返す"""

    system = (
        "あなたは恋愛・人間関係・自己成長をテーマにしたSEOライターです。"
        "読者の悩みに深く刺さり、検索上位を狙える記事を書きます。"
        "ミリテク思考OS（相対×抽象×全）で本質を言語化し、"
        "哲学的視点と実用的なアドバイスを融合させます。"
    )

    prompt = f"""以下のキーワードでSEO記事を書いてください。

キーワード: {keyword}
ジャンル: {niche}
著者ペンネーム: {author}

要件:
- 見出しはH2・H3を使いSEOに最適化
- 2000〜3000文字
- 読者の悩みを冒頭で深く言語化
- 哲学的な本質（「全」＝普遍的真理）から解説
- 具体的な行動アドバイスで締める
- frontmatterは含めない（後で付加する）

本文のみ出力してください。"""

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        body = resp.content[0].text.strip()
        if affiliate_block:
            body += "\n" + affiliate_block

        # タイトル抽出（本文の最初のH1またはH2から）
        title_match = re.search(r"^#{1,2}\s+(.+)$", body, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else keyword

        return {
            "title": title,
            "body": body,
            "keyword": keyword,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
    except Exception as e:
        logger.error("[記事生成] 失敗: %s", e)
        return None


def _save_article(article: dict) -> Path:
    """articles/{date}-{slug}.md として保存"""
    today = str(date.today())
    slug = re.sub(r"[^\w\-]", "-", article["keyword"])[:40].strip("-")
    filename = f"{today}-{slug}.md"
    path = ARTICLES_DIR / filename

    content = (
        f"# {article['title']}\n\n"
        f"**キーワード**: {article['keyword']}\n\n"
        f"---\n\n"
        f"{article['body']}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


# ── 日次処理 ─────────────────────────────────────────────────────────────────
def run_daily(config: dict) -> None:
    logger.info("=== affiliate_言語化SEO 日次処理 開始 ===")

    model = config.get("model", "claude-sonnet-4-6")
    niche = config.get("niche", "恋愛・人間関係・自己成長・哲学")
    author = config.get("author_pen_name", "ミリテク")
    aff_links = config.get("affiliate_links", [])
    articles_per_week = config.get("articles_per_week", 3)

    # 今日記事を生成すべきか判定（週3本ペース）
    published = _load_published()
    cutoff = str(date.today() - timedelta(days=7))
    recent = [r for r in published if r.get("date", "") >= cutoff]
    if len(recent) >= articles_per_week:
        logger.info("今週の記事数（%d本）が目標（%d本）に達しています。本日はスキップ。",
                    len(recent), articles_per_week)
        return

    strategy = _load_strategy()
    keywords = strategy.get("keywords", [niche])

    # 今週すでに使ったキーワードを除外
    used_kws = {r.get("keyword", "") for r in recent}
    remaining = [kw for kw in keywords if kw not in used_kws]
    if not remaining:
        remaining = keywords  # 全消化していたらリセット

    keyword = remaining[0]
    aff_block = _affiliate_block(aff_links, keyword)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    article = _generate_article(client, model, keyword, niche, aff_block, author)
    if not article:
        _notify("❌ [affiliate_言語化SEO] 記事生成失敗", f"キーワード: {keyword}", urgent=True)
        sys.exit(1)

    path = _save_article(article)
    record = {
        "date": str(date.today()),
        "title": article["title"],
        "keyword": keyword,
        "filename": path.name,
    }
    _append_published(record)

    logger.info("記事保存: %s", path.name)
    _notify(
        "✅ [affiliate_言語化SEO] 記事生成完了",
        f"タイトル: {article['title']}\n"
        f"キーワード: {keyword}\n"
        f"今週: {len(recent)+1}/{articles_per_week}本",
    )
    logger.info("=== 日次処理 完了 ===")


# ── 週次処理 ─────────────────────────────────────────────────────────────────
def run_weekly(config: dict) -> None:
    logger.info("=== affiliate_言語化SEO 週次処理 開始 ===")

    model = config.get("model", "claude-sonnet-4-6")
    niche = config.get("niche", "恋愛・人間関係・自己成長・哲学")
    published = _load_published()
    strategy = _load_strategy()

    # 今週の成果サマリー
    cutoff = str(date.today() - timedelta(days=7))
    recent = [r for r in published if r.get("date", "") >= cutoff]
    total = len(published)

    # Claude でキーワード戦略を更新
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"""アフィリエイト×SEOブログのキーワード戦略を更新してください。

ジャンル: {niche}
今週の投稿数: {len(recent)}本
累計投稿数: {total}本
現在のキーワードリスト: {strategy.get('keywords', [])}

日本語で検索需要が高く競合が少ないロングテールキーワードを10個提案してください。
JSONリストのみ出力してください（例: ["キーワード1", "キーワード2", ...]）"""

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            new_kws = json.loads(m.group())
            strategy["keywords"] = new_kws[:10]
            strategy["last_updated"] = str(date.today())
            _save_strategy(strategy)
            logger.info("キーワード戦略更新: %s", new_kws[:5])
    except Exception as e:
        logger.warning("[週次] キーワード更新失敗: %s", e)

    _notify(
        "📊 [affiliate_言語化SEO] 週次レポート",
        f"今週: {len(recent)}本投稿 / 累計: {total}本\n"
        f"キーワード更新: {strategy.get('last_updated', '—')}",
    )
    logger.info("=== 週次処理 完了 ===")


# ── エントリーポイント ────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args()

    config = _load_config()
    monthly_limit = config.get("monthly_cost_limit", 500)

    # コスト簡易チェック（cost_tracker.jsonがあれば）
    cost_path = DATA_DIR / "cost_tracker.json"
    if cost_path.exists():
        try:
            tracker = json.loads(cost_path.read_text(encoding="utf-8"))
            if (tracker.get("month") == str(date.today())[:7]
                    and tracker.get("total_jpy", 0) >= monthly_limit):
                logger.error("月間コスト上限（¥%s）に達しました。スキップします。", monthly_limit)
                return
        except Exception:
            pass

    if args.mode == "daily":
        run_daily(config)
    else:
        run_weekly(config)


if __name__ == "__main__":
    main()
