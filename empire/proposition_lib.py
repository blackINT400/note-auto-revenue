"""
empire/proposition_lib.py — 行動確信プロトコル: 証明済み命題ライブラリ

命題 → 全（普遍的真理）→ 1（具体翻訳）→ 確信度 → 行動の5ステップを管理する。
owner/proven_propositions.json にローカル蓄積（.gitignore 対象）。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
PROPOSITIONS_PATH = PROJECT_ROOT / "owner" / "proven_propositions.json"

CONFIDENCE_AUTO = 80   # ≥80%: 自動実行
CONFIDENCE_EXP  = 50   # 50-79%: 実験として実行し報告
                        # <50%: オーナーに確認


# ── ファイル操作 ────────────────────────────────────────────────────────────────

def load_propositions() -> dict:
    default: dict = {"propositions": []}
    if not PROPOSITIONS_PATH.exists():
        return default
    try:
        return json.loads(PROPOSITIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_propositions(data: dict) -> None:
    PROPOSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROPOSITIONS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 類似命題検索 ────────────────────────────────────────────────────────────────

def find_similar(proposition: str, data: dict, top_n: int = 3) -> list[dict]:
    """キーワード重複スコアで類似命題を返す"""
    words = set(proposition.replace("、", " ").replace("。", " ").split())
    scored: list[tuple[int, dict]] = []
    for p in data.get("propositions", []):
        p_words = set(
            p.get("proposition", "").replace("、", " ").replace("。", " ").split()
        )
        overlap = len(words & p_words)
        if overlap > 0:
            scored.append((overlap, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:top_n]]


# ── Claude による証明 ────────────────────────────────────────────────────────────

def prove_proposition(
    client: anthropic.Anthropic,
    model: str,
    proposition: str,
    observation: str,
    master_os: str,
    domain: str = "",
    similar: list[dict] | None = None,
) -> dict:
    """
    命題を全（普遍的真理）で証明する。
    戻り値: confidence / universal_truth / action を含む dict
    """
    similar_str = ""
    if similar:
        similar_str = "\n\n== 類似の過去証明（流用可能）==\n" + "\n".join(
            f"- 命題: {p.get('proposition', '')} "
            f"/ 確信度: {p.get('confidence', 0)}% "
            f"/ 全: {p.get('universal_truth', '')}"
            for p in similar
        )

    prompt = f"""以下の命題を普遍的真理（全）で証明してください。

命題: {proposition}
観測データ: {observation}
ドメイン: {domain or "コンテンツ・ビジネス"}
{similar_str}

== オーナーの思考OS（参考）==
{master_os[:800]}

以下の構造でJSONのみ出力してください（前置き不要）:
{{
  "proposition": "{proposition}",
  "universal_truth": "この命題と一致する普遍的真理（100字以内）",
  "why_this_domain": "なぜこのジャンルでこう現れるか（80字以内）",
  "translatable_to": ["翻訳できる他ジャンル1", "ジャンル2"],
  "confidence": 確信度0から100の整数,
  "confidence_reason": "確信度の根拠（50字以内）",
  "action": "この証明から導かれる次の具体的行動（80字以内）"
}}"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"JSON抽出失敗: {text[:200]}")
        return json.loads(m.group())
    except Exception as e:
        logger.warning("[確信プロトコル] 証明失敗: %s", e)
        return {
            "proposition": proposition,
            "universal_truth": "証明失敗",
            "why_this_domain": "",
            "translatable_to": [],
            "confidence": 0,
            "confidence_reason": f"APIエラー: {str(e)[:50]}",
            "action": "オーナーに確認",
        }


# ── ライブラリへの追記 ──────────────────────────────────────────────────────────

def add_proposition(
    data: dict,
    proof: dict,
    domain: str,
    action_taken: str = "",
) -> dict:
    """証明結果をライブラリに追記してレコードを返す"""
    propositions = data.setdefault("propositions", [])
    new_id = f"prop_{len(propositions) + 1:03d}"
    record: dict = {
        "id": new_id,
        "date": str(date.today()),
        "proposition": proof.get("proposition", ""),
        "universal_truth": proof.get("universal_truth", ""),
        "why_this_domain": proof.get("why_this_domain", ""),
        "translatable_to": proof.get("translatable_to", []),
        "domain": domain,
        "confidence": proof.get("confidence", 0),
        "confidence_reason": proof.get("confidence_reason", ""),
        "action": proof.get("action", ""),
        "action_taken": action_taken,
        "result": "",
        "accuracy": 0,
    }
    propositions.append(record)
    save_propositions(data)
    return record


def update_result(prop_id: str, result: str, accuracy: int) -> None:
    """実行後の結果・精度を後から更新する"""
    data = load_propositions()
    for p in data.get("propositions", []):
        if p.get("id") == prop_id:
            p["result"] = result
            p["accuracy"] = accuracy
            break
    save_propositions(data)


# ── 出版記録システム ────────────────────────────────────────────────────────────

PUBLICATION_DIR = PROJECT_ROOT / "owner" / "publication"
PROOF_LOG_PATH  = PUBLICATION_DIR / "proof_log.md"
MILESTONES_PATH = PUBLICATION_DIR / "milestones.md"


def append_proof_log(
    proposition: str,
    universal_truth: str,
    result: str,
    context: str = "",
    days_elapsed: int | None = None,
) -> None:
    """
    proof_log.md に「言語化→証明→現実化」の1エントリを追記する。
    毎週の証明サイクル・問題解決の都度呼ぶ。
    """
    PUBLICATION_DIR.mkdir(parents=True, exist_ok=True)
    today = str(date.today())
    elapsed_str = f"{days_elapsed}日" if days_elapsed is not None else "計測中"

    entry = (
        f"\n## {today}"
        + (f"（{context}）" if context else "")
        + f"\n言語化した命題: {proposition}"
        f"\n証明（普遍的真理）: {universal_truth}"
        f"\n現実化した結果: {result or '（記録待ち）'}"
        f"\n所要時間: {elapsed_str}"
        f"\n\n---"
    )

    if PROOF_LOG_PATH.exists():
        PROOF_LOG_PATH.write_text(
            PROOF_LOG_PATH.read_text(encoding="utf-8") + "\n" + entry,
            encoding="utf-8",
        )
    else:
        header = (
            "# 言語化→証明→現実化の記録\n"
            "# 出版の実証データ。毎週自動追記される。\n\n---\n"
        )
        PROOF_LOG_PATH.write_text(header + entry, encoding="utf-8")

    logger.debug("[出版記録] proof_log.md に追記: %s", proposition[:40])


def record_milestone(event: str, detail: str = "", value: str = "") -> None:
    """
    milestones.md にマイルストーンを追記する。
    初収益・フェーズ移行・証明の現実化などの節目に呼ぶ。
    """
    PUBLICATION_DIR.mkdir(parents=True, exist_ok=True)
    today = str(date.today())

    entry = f"| {today} | {event} | {value or '—'} | {detail} |\n"

    if MILESTONES_PATH.exists():
        content = MILESTONES_PATH.read_text(encoding="utf-8")
        # テーブル末尾に追記
        MILESTONES_PATH.write_text(content + entry, encoding="utf-8")
    else:
        header = (
            "# 達成記録（出版の根拠データ）\n\n"
            "| 日付 | 出来事 | 数値・詳細 | 意味 |\n"
            "|------|--------|-----------|------|\n"
        )
        MILESTONES_PATH.write_text(header + entry, encoding="utf-8")

    logger.info("[出版記録] マイルストーン記録: %s — %s", event, value)


# ── ユーティリティ ──────────────────────────────────────────────────────────────

def confidence_label(confidence: int) -> str:
    if confidence >= CONFIDENCE_AUTO:
        return f"✅ 確信（{confidence}%）→ 自動実行"
    elif confidence >= CONFIDENCE_EXP:
        return f"⚠️ 実験中（{confidence}%）→ 報告して実行"
    else:
        return f"❌ 保留（{confidence}%）→ オーナー確認"


def get_top_patterns(data: dict, top_n: int = 3) -> list[dict]:
    """精度更新済みの命題を精度順で返す"""
    proven = [p for p in data.get("propositions", []) if p.get("accuracy", 0) > 0]
    proven.sort(key=lambda x: x.get("accuracy", 0), reverse=True)
    return proven[:top_n]


# ── Discord フォーマット ────────────────────────────────────────────────────────

def format_daily_discord(data: dict, today_proofs: list[dict]) -> str:
    """日次Discordレポート用セクション"""
    total = len(data.get("propositions", []))
    accuracies = [
        p.get("accuracy", 0)
        for p in data.get("propositions", [])
        if p.get("accuracy", 0) > 0
    ]
    avg_acc = round(sum(accuracies) / len(accuracies), 1) if accuracies else 0
    top3 = get_top_patterns(data)

    lines = [
        "━━━━━━━━━━━━━━━",
        "📐 証明済み行動ログ",
        "━━━━━━━━━━━━━━━",
    ]

    if today_proofs:
        for p in today_proofs[:3]:
            conf = p.get("confidence", 0)
            lines += [
                "",
                f"命題: {p.get('proposition', '—')}",
                f"全: {p.get('universal_truth', '—')}",
                f"確信度: {confidence_label(conf)}",
            ]
    else:
        lines += ["", "（本日の新規証明なし）"]

    lines += [
        "",
        f"ライブラリ累計: {total}件 / 平均精度: {avg_acc}%",
    ]

    if top3:
        lines += ["", "精度 TOP3:"]
        for i, p in enumerate(top3, 1):
            prop = p.get("proposition", "?")[:28]
            lines.append(f"  {i}. {prop}… 精度{p.get('accuracy', 0)}%")

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_weekly_discord(data: dict, this_week_ids: list[str]) -> str:
    """週次Discordレポート用セクション"""
    all_map = {p["id"]: p for p in data.get("propositions", [])}
    week_props = [all_map[i] for i in this_week_ids if i in all_map]
    missed = [p for p in week_props if 0 < p.get("accuracy", 0) < 50]

    lines = [
        "━━━━━━━━━━━━━━━",
        "🔬 今週の証明サマリー",
        "━━━━━━━━━━━━━━━",
        "",
        "【新たに証明された命題】",
    ]

    if week_props:
        for p in week_props:
            lines.append(f"・{p.get('proposition', '?')} （{p.get('confidence', 0)}%）")
    else:
        lines.append("（今週の新規証明なし）")

    if missed:
        lines += ["", "【証明が外れたもの（再証明が必要）】"]
        for p in missed:
            lines += [
                f"命題: {p.get('proposition', '?')}",
                f"精度: {p.get('accuracy', 0)}% → 全のレベルに戻って再言語化",
            ]

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)
