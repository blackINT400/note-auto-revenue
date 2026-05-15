"""
phase_manager.py — ビジネスフェーズ自動判定
集客→信頼→収益化 の順序を強制するガード。
"""
from __future__ import annotations
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── フェーズ定義 ──────────────────────────────────────────────────────────────
PHASE_0 = 0  # 種まき: 無料記事量産のみ
PHASE_1 = 1  # 芽吹き: 有料記事・アフィリエイト解禁
PHASE_2 = 2  # 成長: 価格最適化・新事業検討
PHASE_3 = 3  # 拡大: 帝国アーキテクチャ全起動

PHASE_NAMES = {
    PHASE_0: "フェーズ0（種まき）",
    PHASE_1: "フェーズ1（芽吹き）",
    PHASE_2: "フェーズ2（成長）",
    PHASE_3: "フェーズ3（拡大）",
}


def _count_published(data_dir: Path) -> int:
    """published.jsonl の行数（投稿済み記事数）を返す"""
    p = data_dir / "data" / "published.jsonl"
    if not p.exists():
        return 0
    count = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rec = json.loads(line)
                    # draft_ready も含めてカウント（生成済みならOK）
                    count += 1
                except Exception:
                    pass
    return count


def _get_monthly_revenue(portfolio: dict) -> float:
    """portfolio.yaml から今月の総収益を返す"""
    return portfolio.get("empire_kpi", {}).get("total_monthly_revenue", 0.0)


def detect_phase(portfolio: dict, project_root: Path) -> dict:
    """
    現在のフェーズを判定して返す。

    Returns:
        {
          "phase": int,
          "name": str,
          "article_count": int,
          "monthly_revenue": float,
          "allowed": list[str],   # 実行可能なアクション
          "blocked": list[str],   # ブロックされたアクション
          "message": str,
        }
    """
    # 記事数: 全事業の合計
    article_count = 0
    for biz in portfolio.get("businesses", []):
        biz_dir = project_root / biz.get("data_dir", ".")
        article_count += _count_published(biz_dir)

    monthly_revenue = _get_monthly_revenue(portfolio)

    # フォロワー数は現状自動取得不可のため 0 で保守的に判定
    followers = 0  # TODO: note API でフォロワー数取得できれば更新

    # ── フェーズ判定 ──
    if article_count >= 10 and followers >= 50:
        if monthly_revenue >= 10000:
            phase = PHASE_3
        elif monthly_revenue >= 3000:
            phase = PHASE_2
        else:
            phase = PHASE_1
    else:
        phase = PHASE_0

    # ── 実行可能・ブロックリスト ──
    allowed: list[str] = ["free_article_generate", "free_article_post", "trend_scout", "dashboard_update"]
    blocked: list[str] = []

    if phase >= PHASE_1:
        allowed += ["paid_article_generate", "paid_article_post", "affiliate_tag_insert"]
    else:
        blocked += ["paid_article_generate", "paid_article_post", "affiliate_tag_insert"]

    if phase >= PHASE_2:
        allowed += ["price_optimization", "new_business_plan"]
    else:
        blocked += ["price_optimization", "new_business_plan"]

    if phase >= PHASE_3:
        allowed += ["new_business_launch", "empire_scale"]
    else:
        blocked += ["new_business_launch", "empire_scale"]

    # ── メッセージ生成 ──
    next_phase_hints = {
        PHASE_0: f"有料機能解禁まで: あと {max(0, 10 - article_count)} 記事 / フォロワー {max(0, 50 - followers)} 人",
        PHASE_1: f"フェーズ2移行まで: 月収 ¥3,000 達成が必要（現在 ¥{monthly_revenue:.0f}）",
        PHASE_2: f"フェーズ3移行まで: 月収 ¥10,000 達成が必要（現在 ¥{monthly_revenue:.0f}）",
        PHASE_3: "最終フェーズ稼働中",
    }

    result = {
        "phase": phase,
        "name": PHASE_NAMES[phase],
        "article_count": article_count,
        "followers": followers,
        "monthly_revenue": monthly_revenue,
        "allowed": allowed,
        "blocked": blocked,
        "next_hint": next_phase_hints[phase],
        "message": (
            f"[PhaseManager] {PHASE_NAMES[phase]} | "
            f"記事 {article_count} 本 | フォロワー {followers} 人 | "
            f"月収 ¥{monthly_revenue:.0f} | {next_phase_hints[phase]}"
        ),
    }

    logger.info(result["message"])
    return result


def guard(action: str, phase_info: dict) -> bool:
    """
    指定アクションがフェーズ的に許可されているか確認する。
    ブロックされていれば False を返しログを出す。
    """
    if action in phase_info["blocked"]:
        logger.warning(
            "[PhaseManager] BLOCKED: '%s' は %s では実行不可。%s",
            action, phase_info["name"], phase_info["next_hint"],
        )
        return False
    return True


def phase_report(phase_info: dict) -> str:
    """Slack/ログ用のフェーズレポート文字列を生成する"""
    p = phase_info
    lines = [
        f"📊 {p['name']}",
        f"  記事数: {p['article_count']} 本",
        f"  月収: ¥{p['monthly_revenue']:.0f}",
        f"  次のステップ: {p['next_hint']}",
        f"  解禁済み: {', '.join(p['allowed'])}",
    ]
    if p["blocked"]:
        lines.append(f"  ブロック中: {', '.join(p['blocked'])}")
    return "\n".join(lines)
