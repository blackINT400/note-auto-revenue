"""
empire/utils.py: 帝国エージェント共通ユーティリティ
全エージェントがここからポートフォリオ操作・コスト記録・モデル取得・Discord通知を行う
"""
import json
import logging
import os
from datetime import date
from pathlib import Path

import requests
import yaml

EMPIRE_DIR = Path(__file__).parent
PROJECT_ROOT = EMPIRE_DIR.parent

# ── .env 自動読み込み（ローカル開発用）────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_path = PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass
PORTFOLIO_PATH = EMPIRE_DIR / "portfolio.yaml"
EMPIRE_COST_PATH = EMPIRE_DIR / "data" / "empire_cost.json"

# ── Discord通知 ────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

logger = logging.getLogger(__name__)


def notify(title: str, body: str, urgent: bool = False) -> None:
    """Discord embed で通知する。urgent=True で赤色表示。"""
    if not DISCORD_WEBHOOK_URL:
        logger.debug("DISCORD_WEBHOOK_URL 未設定 — 通知をスキップします")
        return
    color = 0xFF4444 if urgent else 0x1D9E75
    # Discord embed の description は 4096 文字上限
    desc = body[:4000] + "\n…（省略）" if len(body) > 4000 else body
    payload = {
        "embeds": [{
            "title": title[:256],
            "description": desc,
            "color": color,
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.warning("Discord通知失敗: %d %s", resp.status_code, resp.text[:100])
    except Exception as exc:
        logger.warning("Discord通知エラー: %s", exc)

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


def load_portfolio() -> dict:
    return yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def save_portfolio(portfolio: dict):
    PORTFOLIO_PATH.write_text(
        yaml.dump(portfolio, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def record_empire_cost(agent: str, input_tokens: int, output_tokens: int) -> float:
    """帝国エージェント（CEO/Scout/Launcher）のAPIコストを記録して累計を返す（円）"""
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    jpy = usd * JPY_RATE
    current_month = str(date.today())[:7]

    EMPIRE_COST_PATH.parent.mkdir(exist_ok=True)
    if EMPIRE_COST_PATH.exists():
        try:
            tracker = json.loads(EMPIRE_COST_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            tracker = {}
        if tracker.get("month") != current_month:
            tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}
    else:
        tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}

    tracker["total_jpy"] = round(tracker["total_jpy"] + jpy, 2)
    tracker["calls"].append({
        "date": str(date.today()),
        "agent": agent,
        "cost_jpy": round(jpy, 2),
    })
    EMPIRE_COST_PATH.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")
    return tracker["total_jpy"]


def get_empire_month_cost() -> float:
    if not EMPIRE_COST_PATH.exists():
        return 0.0
    try:
        tracker = json.loads(EMPIRE_COST_PATH.read_text(encoding="utf-8"))
        if tracker.get("month") != str(date.today())[:7]:
            return 0.0
        return tracker.get("total_jpy", 0.0)
    except (json.JSONDecodeError, OSError):
        return 0.0


def get_model() -> str:
    """プロジェクトルートの config.yaml からモデル名を取得"""
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            return cfg.get("model", "claude-sonnet-4-6")
        except Exception:
            pass
    return "claude-sonnet-4-6"


def get_empire_cost_limit() -> float:
    """portfolio.yaml から月間コスト上限を取得"""
    try:
        portfolio = load_portfolio()
        return float(portfolio.get("revenue_pool", {}).get("monthly_cost_limit", 5000))
    except Exception:
        return 5000.0


def get_master_os() -> str:
    """owner/context_prompt.md からミリテク思考OSマスタープロンプトを返す"""
    path = PROJECT_ROOT / "owner" / "context_prompt.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def solve_with_os(
    problem: str,
    client=None,
    model: str | None = None,
) -> dict:
    """
    ミリテク思考OS × 6ステップで問題を哲学的に解決する。
    empire_main.py の MAX_RETRIES 使い切り時に呼ばれる。

    STEP1: 問題を一文で言語化（命題化）
    STEP2: 全（普遍的真理）への接続
    STEP3: 背理法で不要な仮説を削る
    STEP4: 重力（影響範囲×確信度×緊急性）で根本原因を特定
    STEP5: 言語化して確信してから解決策を実行
    STEP6: 結果を proof_log.md に記録
    """
    import json as _json
    import re as _re

    import anthropic as _anthropic

    try:
        from empire.proposition_lib import append_proof_log, find_similar, load_propositions
    except ImportError:
        append_proof_log = None  # type: ignore[assignment]
        find_similar = None      # type: ignore[assignment]
        load_propositions = None # type: ignore[assignment]

    master_os = get_master_os()

    similar_str = ""
    if load_propositions and find_similar:
        try:
            props_data = load_propositions()
            similar = find_similar(problem[:80], props_data)
            if similar:
                similar_str = "\n\n== 類似の過去解決策（proven_propositions より）==\n" + "\n".join(
                    f"- 命題: {p.get('proposition', '')} "
                    f"/ 確信度: {p.get('confidence', 0)}% "
                    f"/ 解決策: {p.get('action', '')}"
                    for p in similar
                )
        except Exception:
            pass

    prompt = f"""ミリテク思考OS（全一・相対・翻訳）を使って、以下の問題を6ステップで解決してください。

問題: {problem}
{similar_str}

== 思考OS（参考）==
{master_os[:600]}

以下の構造でJSONのみ出力してください（前置き不要）:
{{
  "proposition": "問題を一文で言語化した命題",
  "universal_truth": "この問題と一致する普遍的真理（全への接続）",
  "hypotheses_eliminated": ["背理法で削った仮説1", "仮説2"],
  "root_cause": "重力（影響範囲×確信度×緊急性）で見た根本原因",
  "solution": "言語化して確信した解決策（具体的行動）",
  "confidence": 確信度0から100の整数,
  "action": "今すぐ実行する次の一手（60字以内）",
  "needs_owner": true または false
}}"""

    try:
        if client is None:
            client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        if model is None:
            model = get_model()

        resp = client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not m:
            raise ValueError(f"JSON抽出失敗: {text[:200]}")
        result = _json.loads(m.group())

        # STEP6: proof_log.md に記録
        if append_proof_log:
            try:
                append_proof_log(
                    proposition=result.get("proposition", problem[:80]),
                    universal_truth=result.get("universal_truth", ""),
                    result=result.get("solution", ""),
                    context="エラー自己解決",
                )
            except Exception:
                pass

        return result

    except Exception as e:
        logger.warning("[思考OS] solve_with_os 失敗: %s", e)
        return {
            "proposition": problem[:80],
            "universal_truth": "解決失敗",
            "hypotheses_eliminated": [],
            "root_cause": "API接続エラー",
            "solution": "オーナーに手動確認を依頼",
            "confidence": 0,
            "action": "オーナーに報告",
            "needs_owner": True,
        }
