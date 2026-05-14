"""
Creator: Claude を使ってデジタル商品コンテンツを生成し保存する
"""
import json
import logging
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


def _slugify(title: str) -> str:
    """日本語タイトルを ASCII スラッグに変換する"""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_str).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    if not slug:
        slug = f"product-{date.today()}"
    return slug[:50]


def _build_content_prompt(opportunity: dict) -> str:
    """商品タイプに応じたコンテンツ生成プロンプトを構築する"""
    title = opportunity.get("title", "デジタル商品")
    ptype = opportunity.get("type", "ノウハウPDF")
    audience = opportunity.get("target_audience", "初心者")
    keywords = opportunity.get("keywords", [])

    base = (
        f"あなたはプロのデジタル商品クリエイターです。\n"
        f"以下の仕様でGumroad向けデジタル商品のコンテンツを日本語で作成してください。\n\n"
        f"商品タイトル: {title}\n"
        f"商品タイプ: {ptype}\n"
        f"ターゲット: {audience}\n"
        f"キーワード: {', '.join(keywords)}\n\n"
        f"文字数: 2000〜3000字のMarkdown形式\n\n"
    )

    if ptype == "テンプレート集":
        structure = (
            "構成:\n"
            "1. はじめに（使い方・活用シーン）\n"
            "2. テンプレート1〜5（各テンプレートの説明＋本文例）\n"
            "3. カスタマイズのヒント\n"
            "4. よくある質問\n"
        )
    elif ptype == "プロンプト集":
        structure = (
            "構成:\n"
            "1. はじめに（AI活用の基本）\n"
            "2. プロンプト一覧（20件以上、各プロンプトにユースケースを添付）\n"
            "3. 効果的な使い方のコツ\n"
            "4. 応用テクニック\n"
        )
    elif ptype == "チェックリスト":
        structure = (
            "構成:\n"
            "1. このチェックリストの使い方\n"
            "2. チェック項目一覧（カテゴリ別、各項目に説明付き）\n"
            "3. 実践ガイド（ステップバイステップ）\n"
            "4. 完了後の次のステップ\n"
        )
    else:  # ノウハウPDF
        structure = (
            "構成:\n"
            "1. はじめに（読むべき人・得られる成果）\n"
            "2. 基礎知識（3〜5つのポイント）\n"
            "3. 実践ステップ（番号付きリスト）\n"
            "4. よくある失敗と対策\n"
            "5. まとめ・次のアクション\n"
        )

    return base + structure + "\nMarkdownのみ返してください。"


def _build_metadata_prompt(opportunity: dict, content: str) -> str:
    """Gumroad 出品用メタデータ生成プロンプト"""
    return (
        f"以下のデジタル商品コンテンツに基づいて、Gumroad出品用のメタデータを生成してください。\n\n"
        f"コンテンツ（先頭500字）:\n{content[:500]}\n\n"
        f"元の機会情報:\n{json.dumps(opportunity, ensure_ascii=False)}\n\n"
        f"以下のJSON形式で返してください:\n"
        f'{{\n'
        f'  "product_title": "商品名（50字以内）",\n'
        f'  "tagline": "キャッチコピー（20字以内）",\n'
        f'  "description": "商品説明（300字、Gumroadの説明欄用）",\n'
        f'  "tags": ["タグ1", "タグ2", "タグ3", "タグ4", "タグ5"],\n'
        f'  "price_jpy": {opportunity.get("price_jpy", 980)}\n'
        f'}}\n\n'
        f"JSONのみ返してください。"
    )


def _generate_product(
    client: anthropic.Anthropic,
    model: str,
    opportunity: dict,
    data_dir: Path,
) -> dict:
    """1つの機会からコンテンツとメタデータを生成し保存する"""
    # コンテンツ生成
    content_prompt = _build_content_prompt(opportunity)
    content_response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": content_prompt}],
    )
    content = content_response.content[0].text.strip()
    logger.info(
        "Content generated: %d chars (tokens in=%d, out=%d)",
        len(content),
        content_response.usage.input_tokens,
        content_response.usage.output_tokens,
    )

    # メタデータ生成
    meta_prompt = _build_metadata_prompt(opportunity, content)
    meta_response = client.messages.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": meta_prompt}],
    )
    meta_raw = meta_response.content[0].text.strip()
    match = re.search(r"\{.*\}", meta_raw, re.DOTALL)
    if match:
        meta_raw = match.group(0)
    metadata = json.loads(meta_raw)

    # スラッグ生成・ファイル保存
    slug = _slugify(metadata.get("product_title", opportunity.get("title", "product")))
    slug = f"{date.today()}_{slug}"

    content_dir = data_dir / "data" / "content"
    content_dir.mkdir(parents=True, exist_ok=True)

    content_path = content_dir / f"{slug}.md"
    with open(content_path, "w", encoding="utf-8") as f:
        f.write(f"# {metadata.get('product_title', '')}\n\n")
        f.write(content)

    metadata["slug"] = slug
    metadata["content_path"] = str(content_path)
    metadata["opportunity"] = opportunity
    metadata["created_at"] = str(date.today())

    meta_path = content_dir / f"{slug}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    logger.info("Saved product content to %s", content_path)
    return metadata


def run_creator(config: dict, data_dir: Path, opportunities: list) -> list[dict]:
    """機会リストからデジタル商品コンテンツを生成する（1日最大N件）"""
    model = config.get("model", "claude-sonnet-4-6")
    max_per_day = config.get("max_products_per_day", 1)
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    products = []
    for opp in opportunities[:max_per_day]:
        try:
            product = _generate_product(client, model, opp, data_dir)
            products.append(product)
        except Exception as exc:
            logger.error("Failed to generate product for '%s': %s", opp.get("title"), exc)

    logger.info("Created %d products", len(products))
    return products
