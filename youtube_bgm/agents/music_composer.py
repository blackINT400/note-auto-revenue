"""
music_composer.py: BGM楽曲構成・プロンプト生成エージェント
トレンドコンセプトから楽曲生成プロンプトと構成を作成する
"""
import json
import logging
import os
import re

import anthropic

logger = logging.getLogger(__name__)

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


def _calc_cost_jpy(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    return usd * JPY_RATE


def compose_music_package(concept: dict, dry_run: bool = False) -> dict:
    """コンセプトから楽曲生成プロンプトと構成を作成"""
    if dry_run:
        logger.info("[DRY-RUN] モック楽曲パッケージを使用")
        return {
            "suno_prompt": "smooth jazz, 75bpm, piano, soft bass, muted trumpet, late night lounge",
            "udio_prompt": "relaxing smooth jazz, piano melody, gentle bass, 75bpm, cozy night",
            "structure": {"bpm": 75, "key": "F major", "instruments": ["piano", "bass", "trumpet"], "sections": ["intro", "main_loop", "outro"]},
            "visual_concept": "luxury living room: morning forest",
            "thumbnail_text": "朝の室内ジャズ BGM",
            "description_jp": "朝日が差し込む、森と湖が見える高級リビングルーム。\n静かに仕事を始めたい朝、コーヒーを片手にゆっくりしたい時間に。\nSmooth JazzとAmbientを融合させた、集中と癒しのBGMです。",
            "tags": ["作業BGM", "smooth jazz", "朝BGM", "집중력"],
            "cost_jpy": 0.0,
            "success": True,
            "concept": concept,
        }

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

    prompt = f"""あなたはBGM作曲の専門家です。以下のコンセプトに基づき楽曲パッケージを設計してください。

コンセプト:
- タイトル: {concept.get('title')}
- ジャンル: {concept.get('genre')}
- ムード: {concept.get('mood')}
- 尺: {concept.get('duration_minutes', 60)}分

以下のJSON形式のみで出力:
{{
  "suno_prompt": "Suno AIへの楽曲生成プロンプト（英語・詳細）",
  "udio_prompt": "Udio AIへの楽曲生成プロンプト（英語・詳細）",
  "structure": {{
    "bpm": 75,
    "key": "F major",
    "instruments": ["piano", "bass"],
    "sections": ["intro", "main_loop", "outro"]
  }},
  "visual_concept": "動画の映像コンセプト（背景・シーン）",
  "thumbnail_text": "サムネイルのメインテキスト（日本語・短く）",
  "description_jp": "YouTube概要欄冒頭3行（以下フォーマット厳守）:\n1行目: 情景の詩的な描写（例: 朝日が差し込む、森と湖が見える高級リビングルーム。）\n2行目: どんな時間・気持ちの人向けか（例: 静かに仕事を始めたい朝、コーヒーを片手にゆっくりしたい時間に。）\n3行目: 音楽のスタイル説明（例: Smooth JazzとAmbientを融合させた、集中と癒しのBGMです。）\n（3行で500字以内）",
  "tags": ["日本語タグ", "english tag", "한국어태그"]
}}"""

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    cost_jpy = _calc_cost_jpy(response.usage.input_tokens, response.usage.output_tokens)

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"JSON抽出失敗: {text[:200]}")

    result = json.loads(m.group())
    result["cost_jpy"] = round(cost_jpy, 2)
    result["success"] = True
    result["concept"] = concept
    logger.info(f"楽曲パッケージ生成完了: {concept.get('title')}")
    return result
