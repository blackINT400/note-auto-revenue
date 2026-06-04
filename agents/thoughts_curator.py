"""
ThoughtsCurator: owner/thoughts.md → owner/context_prompt.md 統合パイプライン

思考の進化を追跡し、context_prompt.mdを常に最新・最核心の状態に保つ。

処理フロー:
  1. thoughts.md の変更を検知（SHA256ハッシュ比較）
  2. Claude API で意味的統合
     - 重複の整理（同じ概念を一本化）
     - 矛盾の解決（新しい思考を優先）
     - 新規洞察の組み込み
     API不使用時: 新規日付セクションを末尾追記（フォールバック）
  3. 変更前の context_prompt.md を owner/archive/ に退避
  4. 統合結果を context_prompt.md に書き込む
  5. owner/decisions.md に変更ログを追記
  6. owner/curation_state.json に処理状態を保存

実行:
  python agents/thoughts_curator.py            # 通常実行（変更がある時のみ）
  python agents/thoughts_curator.py --dry-run  # プレビューのみ（ファイル変更なし）
  python agents/thoughts_curator.py --force    # 変更検知なしでも強制実行

注意: owner/ はgitignored のためローカル専用ツールです。
      GitHub Actions には統合されません。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── パス定義 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
THOUGHTS_PATH = PROJECT_ROOT / "owner" / "thoughts.md"
CONTEXT_PATH = PROJECT_ROOT / "owner" / "context_prompt.md"
ARCHIVE_DIR = PROJECT_ROOT / "owner" / "archive"
DECISIONS_PATH = PROJECT_ROOT / "owner" / "decisions.md"
STATE_PATH = PROJECT_ROOT / "owner" / "curation_state.json"

# ── モデル設定 ─────────────────────────────────────────────────────────────────
PRIMARY_MODEL = "claude-opus-4-5"    # 意味的統合のため最高品質を優先
FALLBACK_MODEL = "claude-sonnet-4-6" # Opus失敗時のフォールバック


# ── ユーティリティ ─────────────────────────────────────────────────────────────

def _sha256(text: str) -> str:
    """テキストの短縮SHA256を返す（変更検知用）"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"last_curated_at": "", "thoughts_hash": "", "archive_count": 0}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_curated_at": "", "thoughts_hash": "", "archive_count": 0}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── アーカイブ処理 ─────────────────────────────────────────────────────────────

def _archive_context(today: str) -> Path | None:
    """
    現在の context_prompt.md を owner/archive/ に退避する。
    同日に複数回実行された場合は連番を付加。
    Returns: 保存したアーカイブのパス（context_prompt.md が存在しない場合は None）
    """
    if not CONTEXT_PATH.exists():
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"context_prompt_{today}.md"
    counter = 1
    while archive_path.exists():
        archive_path = ARCHIVE_DIR / f"context_prompt_{today}_{counter:02d}.md"
        counter += 1

    archive_path.write_text(
        CONTEXT_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    logger.info("アーカイブ保存: %s", archive_path.name)
    return archive_path


def _log_to_decisions(summary: str, today: str) -> None:
    """decisions.md にキュレーション変更ログを追記する"""
    entry = (
        f"\n## {today} thoughts.md → context_prompt.md キュレーション\n"
        f"{summary}\n"
    )
    if DECISIONS_PATH.exists():
        existing = DECISIONS_PATH.read_text(encoding="utf-8")
        DECISIONS_PATH.write_text(existing + entry, encoding="utf-8")
    else:
        DECISIONS_PATH.write_text(
            f"# 過去の判断とその理由\n{entry}", encoding="utf-8"
        )


# ── Claude API統合 ─────────────────────────────────────────────────────────────

def _curate_with_claude(
    client: Any,
    model: str,
    thoughts: str,
    context: str,
) -> tuple[str, str]:
    """
    Claude API を使って thoughts.md の内容を context_prompt.md に意味的に統合する。

    Returns:
        (updated_context: str, changes_summary: str)
    """
    prompt = f"""あなたは「ミリテク思考OS」の設計アーキテクトです。

## タスク
オーナーの思考メモ（thoughts.md）を分析し、
現在稼働中の思考OS（context_prompt.md）を最新・最核心の状態に更新してください。

## 統合の4原則
1. **新規洞察の組み込み**: thoughts.md にあってcontext_prompt.md にない核心的ロジックを追加する
2. **重複の整理**: 同じ概念が複数箇所にある場合は最も洗練された表現に一本化する
3. **矛盾の解決**: 古い記述と新しい記述が対立する場合は、より発展した新しい方を採用する
4. **構造の保持**: 「エージェントへの適用」「行動確信プロトコル」などシステム実装に関わるセクションは変更しない

## 現在の context_prompt.md（稼働中OS）
```
{context}
```

## オーナーの最新思考（thoughts.md）
```
{thoughts}
```

## 出力形式
以下のJSON形式で出力してください（コードブロック不要）:

{{
  "updated_context": "（完全な新しいcontext_prompt.mdの内容。マークダウン形式。3000文字以上）",
  "changes_summary": "（変更した内容の箇条書き。decisions.mdへの記録用。以下の形式で）\\n- 追加: ...\\n- 整理: ...\\n- 削除: ...\\n- 保持: ..."
}}

JSONのみ出力してください。説明文は不要です。"""

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # コードブロック除去
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    # JSON抽出
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)

    result = json.loads(raw)
    updated_context = result.get("updated_context", "")
    changes_summary = result.get("changes_summary", "統合完了（詳細不明）")

    logger.info(
        "Claude統合完了: %d文字 → %d文字 (model=%s)",
        len(context), len(updated_context), model,
    )
    return updated_context, changes_summary


# ── フォールバック（API不使用）────────────────────────────────────────────────

def _extract_new_sections(thoughts: str, context: str) -> list[str]:
    """
    thoughts.md の日付付きセクションのうち、
    context_prompt.md にまだ含まれていない新規セクションを返す。
    """
    # thoughts.md の「## YYYY-MM-DD」セクションをすべて抽出
    all_sections = re.findall(
        r"(## \d{4}-\d{2}-\d{2}.*?)(?=\n## \d{4}-\d{2}-\d{2}|\Z)",
        thoughts,
        re.DOTALL,
    )

    # context_prompt.md に含まれている日付を収集
    existing_dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", context))

    new_sections = []
    for section in all_sections:
        dates_in_section = re.findall(r"\d{4}-\d{2}-\d{2}", section)
        if dates_in_section and not any(d in existing_dates for d in dates_in_section):
            new_sections.append(section.strip())

    return new_sections


def _curate_heuristic(thoughts: str, context: str) -> tuple[str, str]:
    """
    API不使用のフォールバック処理。
    新規日付セクションを context_prompt.md 末尾に追記する。
    意味的な統合はできないが、思考の追跡を止めない。
    """
    new_sections = _extract_new_sections(thoughts, context)

    if not new_sections:
        return context, "- 新規セクションなし（変更なし）\n- API不使用フォールバックモード"

    separator = (
        "\n\n---\n\n"
        "## 未統合の新規思考メモ\n"
        "※ Claude API が使用可能になったとき `--force` で完全統合してください\n\n"
    )
    updated = context.rstrip() + separator + "\n\n---\n\n".join(new_sections)

    # 各セクションの最初の見出し行を取得
    section_titles = []
    for s in new_sections[:3]:
        m = re.search(r"## .+", s)
        if m:
            section_titles.append(m.group(0)[:40])

    summary = (
        f"- API不使用フォールバック: {len(new_sections)}セクションを末尾に追記\n"
        f"- 追記セクション: {', '.join(section_titles)}\n"
        "- 完全な意味的統合には `python agents/thoughts_curator.py --force` を実行"
    )
    return updated, summary


# ── メイン処理 ────────────────────────────────────────────────────────────────

def run_curation(
    dry_run: bool = False,
    force: bool = False,
    model: str | None = None,
) -> dict:
    """
    キュレーションのメインエントリポイント。

    Args:
        dry_run: Trueなら変更を保存せず結果を表示のみ
        force: Trueなら変更なしでも強制実行
        model: Claudeモデル指定（Noneなら PRIMARY_MODEL）

    Returns:
        {
          "changed": bool,
          "archive_path": str | None,
          "summary": str,
          "mode": "claude_api" | "heuristic" | "skipped",
        }
    """
    today = str(date.today())

    # ── ファイル存在確認 ─────────────────────────────────────────────────────
    if not THOUGHTS_PATH.exists():
        logger.warning("thoughts.md が見つかりません: %s", THOUGHTS_PATH)
        return {"changed": False, "summary": "thoughts.md なし", "mode": "skipped"}

    thoughts = THOUGHTS_PATH.read_text(encoding="utf-8")
    context = CONTEXT_PATH.read_text(encoding="utf-8") if CONTEXT_PATH.exists() else ""

    # ── 変更検知 ─────────────────────────────────────────────────────────────
    current_hash = _sha256(thoughts)
    state = _load_state()

    if not force and state.get("thoughts_hash") == current_hash:
        logger.info("thoughts.md に変更なし（hash一致）。--force で強制実行できます。")
        return {"changed": False, "summary": "変更なし（スキップ）", "mode": "skipped"}

    logger.info("thoughts.md の変更を検出。キュレーション開始...")

    # ── Claude API 統合 ──────────────────────────────────────────────────────
    updated_context = ""
    summary = ""
    mode = "heuristic"
    chosen_model = model or PRIMARY_MODEL

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            try:
                updated_context, summary = _curate_with_claude(
                    client, chosen_model, thoughts, context
                )
                mode = "claude_api"
            except Exception as exc:
                logger.warning(
                    "%s 失敗 → %s でリトライ: %s",
                    chosen_model, FALLBACK_MODEL, exc,
                )
                updated_context, summary = _curate_with_claude(
                    client, FALLBACK_MODEL, thoughts, context
                )
                mode = "claude_api"
                chosen_model = FALLBACK_MODEL
        except Exception as exc:
            logger.warning("Claude API 失敗、フォールバックへ: %s", exc)
    else:
        logger.info("ANTHROPIC_API_KEY 未設定。フォールバックモードで実行。")

    # ── フォールバック ────────────────────────────────────────────────────────
    if not updated_context:
        updated_context, summary = _curate_heuristic(thoughts, context)
        mode = "heuristic"

    if not updated_context:
        logger.error("キュレーション結果が空でした。処理を中止。")
        return {"changed": False, "summary": "エラー: 空の結果", "mode": "error"}

    # ── dry-run モード ───────────────────────────────────────────────────────
    if dry_run:
        preview = updated_context[:3000]
        if len(updated_context) > 3000:
            preview += f"\n\n... （残り {len(updated_context) - 3000} 文字省略）"
        print("=" * 60)
        print("[DRY RUN] 更新後の context_prompt.md プレビュー")
        print("=" * 60)
        print(preview)
        print("\n" + "=" * 60)
        print("[DRY RUN] 変更サマリー")
        print("=" * 60)
        print(summary)
        print(f"\nmode: {mode}")
        return {
            "changed": True,
            "archive_path": None,
            "summary": summary,
            "mode": mode,
            "dry_run": True,
        }

    # ── 書き込み処理 ─────────────────────────────────────────────────────────
    archive_path = _archive_context(today)
    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_PATH.write_text(updated_context, encoding="utf-8")
    _log_to_decisions(summary, today)

    # 状態保存
    state["last_curated_at"] = today
    state["thoughts_hash"] = current_hash
    state["archive_count"] = state.get("archive_count", 0) + 1
    _save_state(state)

    logger.info(
        "完了: mode=%s model=%s archive=%s",
        mode,
        chosen_model if mode == "claude_api" else "N/A",
        archive_path.name if archive_path else "なし",
    )

    return {
        "changed": True,
        "archive_path": str(archive_path) if archive_path else None,
        "summary": summary,
        "mode": mode,
    }


# ── CLIエントリポイント ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="thoughts.md → context_prompt.md キュレーター"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="変更を保存せずプレビューのみ表示",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="変更検知なしでも強制実行（API回復後に完全統合する際に使用）",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"使用するClaudeモデル（デフォルト: {PRIMARY_MODEL}）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="DEBUGレベルのログを出力",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = run_curation(
        dry_run=args.dry_run,
        force=args.force,
        model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
