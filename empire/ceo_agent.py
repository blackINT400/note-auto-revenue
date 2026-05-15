"""
ceo_agent.py: 全事業を監視・予算配分を自動決定するCEOエージェント

判断は2層構造:
  Layer1: Pythonハードルール（トリガー閾値を確実に適用）
  Layer2: Claude（コンテキスト付きで戦略判断・推論を追加）

目標KPI:
  3ヶ月後: 月収 30,000円
  6ヶ月後: 月収 100,000円
  12ヶ月後: 月収 300,000円
  最大事業数: 10件
"""
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic
import yaml

from empire.utils import (
    EMPIRE_DIR, PROJECT_ROOT,
    get_empire_cost_limit, get_model,
    load_portfolio, record_empire_cost, save_portfolio,
)

logger = logging.getLogger(__name__)

CEO_DECISIONS_LOG = PROJECT_ROOT / "logs" / "ceo_decisions.jsonl"
KPI_HISTORY_PATH = EMPIRE_DIR / "data" / "kpi_history.jsonl"

# 目標KPI（月数→月収（円））
REVENUE_TARGETS = [
    {"months": 3,  "revenue": 30_000},
    {"months": 6,  "revenue": 100_000},
    {"months": 12, "revenue": 300_000},
]
MAX_BUSINESSES = 10

# スケールアップ/縮小/停止の閾値
SCALE_UP_GROWTH_PCT    = 20.0   # 前月比+20%でスケールアップ
SCALE_UP_BUDGET_MULT   = 1.5    # 予算配分の倍率
ROI_HIGH_THRESHOLD     = 200.0  # ROI 200%超で水平展開
ROI_LOW_THRESHOLD      = 50.0   # ROI 50%未満で縮小候補
CONSECUTIVE_GROWTH_3M  = 3      # 3ヶ月連続増加で最優先強化
LOW_ROI_SCALE_DOWN_MO  = 2      # 2ヶ月連続低ROIで縮小
LOW_ROI_KILL_MO        = 3      # 3ヶ月連続低ROIで停止
COST_OVER_KILL_MO      = 2      # 2ヶ月連続コスト>収益で即時停止候補


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KPI収集
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _collect_zenn_kpi(business: dict) -> dict:
    data_dir = PROJECT_ROOT / business.get("data_dir", ".")
    current_month = str(date.today())[:7]

    monthly_cost = 0.0
    cost_path = data_dir / "data/cost_tracker.json"
    if cost_path.exists():
        try:
            tracker = json.loads(cost_path.read_text(encoding="utf-8"))
            if tracker.get("month") == current_month:
                monthly_cost = tracker.get("total_jpy", 0.0)
        except (json.JSONDecodeError, OSError):
            pass

    articles_count, avg_quality = 0, 0.0
    pub_path = data_dir / "logs/published.jsonl"
    if pub_path.exists():
        try:
            month_articles = [
                json.loads(line) for line in pub_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("date", "").startswith(current_month)
            ]
            articles_count = len(month_articles)
            scores = [a.get("quality_score", 0) for a in month_articles if a.get("quality_score", 0) > 0]
            avg_quality = round(sum(scores) / len(scores), 1) if scores else 0.0
        except (json.JSONDecodeError, OSError):
            pass

    monthly_revenue = float(business.get("monthly_revenue", 0))
    roi = round((monthly_revenue - monthly_cost) / max(monthly_cost, 1) * 100, 1)

    return {
        "id": business["id"],
        "type": business["type"],
        "niche": business.get("niche", ""),
        "monthly_revenue": monthly_revenue,
        "monthly_cost": round(monthly_cost, 1),
        "roi": roi,
        "articles_this_month": articles_count,
        "avg_quality_score": avg_quality,
        "status": business.get("status", "active"),
    }


def _collect_generic_kpi(business: dict) -> dict:
    data_dir = PROJECT_ROOT / "businesses" / business["id"] / "data"
    kpi_path = data_dir / "kpi.json"
    if kpi_path.exists():
        try:
            kpi = json.loads(kpi_path.read_text(encoding="utf-8"))
            kpi.setdefault("id", business["id"])
            kpi.setdefault("type", business.get("type", ""))
            kpi.setdefault("status", business.get("status", "active"))
            return kpi
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "id": business["id"],
        "type": business.get("type", ""),
        "niche": business.get("niche", ""),
        "monthly_revenue": float(business.get("monthly_revenue", 0)),
        "monthly_cost": float(business.get("monthly_cost", 0)),
        "roi": float(business.get("roi", 0)),
        "status": business.get("status", "active"),
    }


def collect_all_kpis(businesses: list) -> list:
    kpis = []
    for b in businesses:
        if b.get("status") in ("terminated", "pending_human_approval"):
            continue
        kpi = _collect_zenn_kpi(b) if b.get("type") == "zenn_article" else _collect_generic_kpi(b)
        kpis.append(kpi)
    return kpis


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KPI履歴管理 (empire/data/kpi_history.jsonl)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_kpi_history() -> list:
    """全事業の月次KPI履歴を読み込む"""
    if not KPI_HISTORY_PATH.exists():
        return []
    records = []
    for line in KPI_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _save_monthly_snapshot(kpis: list):
    """今月のKPIスナップショットを保存（同月・同事業は上書き）"""
    current_month = str(date.today())[:7]
    new_ids = {k["id"] for k in kpis}

    # 今月分の既存レコードを除外して再構築
    preserved = []
    if KPI_HISTORY_PATH.exists():
        for line in KPI_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if not (r.get("month") == current_month and r.get("business_id") in new_ids):
                    preserved.append(line)
            except json.JSONDecodeError:
                pass

    new_lines = [
        json.dumps({
            "month": current_month,
            "business_id": k["id"],
            "monthly_revenue": k.get("monthly_revenue", 0),
            "monthly_cost": k.get("monthly_cost", 0),
            "roi": k.get("roi", 0),
            "recorded_at": str(date.today()),
        }, ensure_ascii=False)
        for k in kpis
    ]

    KPI_HISTORY_PATH.parent.mkdir(exist_ok=True)
    KPI_HISTORY_PATH.write_text("\n".join(preserved + new_lines) + "\n", encoding="utf-8")


def _get_business_months(business_id: str, history: list, n: int) -> list:
    """指定事業の直近n ヶ月のレコードを新しい順で返す"""
    records = [r for r in history if r.get("business_id") == business_id]
    records.sort(key=lambda x: x.get("month", ""), reverse=True)
    return records[:n]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer1: Pythonハードルール（トリガー判定）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _analyze_triggers(business_id: str, history: list) -> dict:
    """
    事業の履歴を分析してトリガーフラグを返す。
    履歴が不足している場合は各トリガーが発火しない（新事業保護）。
    """
    months = _get_business_months(business_id, history, 3)
    result = {"history_months": len(months)}

    if not months:
        return result

    cur = months[0]
    prev1 = months[1] if len(months) >= 2 else None
    prev2 = months[2] if len(months) >= 3 else None

    cur_rev  = cur.get("monthly_revenue", 0)
    cur_cost = cur.get("monthly_cost", 0)
    cur_roi  = cur.get("roi", 0)

    # ── スケールアップトリガー ─────────────────────────
    if prev1:
        p1_rev = prev1.get("monthly_revenue", 0)
        if p1_rev > 0 and cur_rev >= p1_rev * (1 + SCALE_UP_GROWTH_PCT / 100):
            result["mom_growth_20pct"] = True
            result["growth_rate_pct"] = round((cur_rev - p1_rev) / p1_rev * 100, 1)

    if prev1 and prev2:
        r0 = cur_rev
        r1 = prev1.get("monthly_revenue", 0)
        r2 = prev2.get("monthly_revenue", 0)
        if r0 > r1 > r2 and r2 >= 0:
            result["consecutive_growth_3m"] = True

    if cur_roi >= ROI_HIGH_THRESHOLD and cur_cost > 0:
        result["roi_over_200pct"] = True

    # ── 縮小・停止トリガー ────────────────────────────
    if prev1:
        p1_roi  = prev1.get("monthly_roi", prev1.get("roi", 0))
        p1_cost = prev1.get("monthly_cost", 0)
        p1_rev  = prev1.get("monthly_revenue", 0)

        # ROI < 50% 2ヶ月連続 → 縮小
        if cur_roi < ROI_LOW_THRESHOLD and p1_roi < ROI_LOW_THRESHOLD and cur_cost > 0:
            result["low_roi_2m"] = True

        # コスト > 収益 2ヶ月連続 → 即時停止候補
        if (cur_cost > cur_rev and p1_cost > p1_rev and cur_cost > 0 and p1_cost > 0):
            result["cost_over_revenue_2m"] = True

    if prev1 and prev2:
        p1_roi = prev1.get("monthly_roi", prev1.get("roi", 0))
        p2_roi = prev2.get("monthly_roi", prev2.get("roi", 0))
        # ROI < 50% 3ヶ月連続 → 停止候補
        if (cur_roi < ROI_LOW_THRESHOLD
                and p1_roi < ROI_LOW_THRESHOLD
                and p2_roi < ROI_LOW_THRESHOLD
                and cur_cost > 0):
            result["low_roi_3m"] = True

    return result


def apply_rule_triggers(kpis: list, history: list) -> dict:
    """
    全事業のトリガーを分析してルールベースの強制アクションを返す。
    戻り値:
      force_scale_up   : 確実に拡大すべき事業IDのリスト
      force_scale_down : 確実に縮小すべき事業IDのリスト
      force_kill       : 停止すべき事業IDのリスト
      expansion_targets: {事業ID: 理由} 水平展開推奨
      trigger_details  : {事業ID: トリガーフラグ辞書}
    """
    force_scale_up    = []
    force_scale_down  = []
    force_kill        = []
    expansion_targets = {}
    trigger_details   = {}

    for kpi in kpis:
        bid = kpi["id"]
        triggers = _analyze_triggers(bid, history)
        trigger_details[bid] = triggers

        # 停止優先（最も重い）
        if triggers.get("cost_over_revenue_2m") or triggers.get("low_roi_3m"):
            if bid not in force_kill:
                force_kill.append(bid)
            reason = "コスト>収益2ヶ月" if triggers.get("cost_over_revenue_2m") else "ROI<50%を3ヶ月連続"
            logger.warning(f"[CEO/Rules] 停止トリガー発火: {bid} ({reason})")

        # 縮小（停止でないとき）
        elif triggers.get("low_roi_2m"):
            force_scale_down.append(bid)
            logger.info(f"[CEO/Rules] 縮小トリガー発火: {bid} (ROI<50%を2ヶ月連続)")

        # 拡大
        if triggers.get("consecutive_growth_3m"):
            if bid not in force_scale_up:
                force_scale_up.append(bid)
            logger.info(f"[CEO/Rules] 最優先拡大トリガー: {bid} (3ヶ月連続増加)")
        elif triggers.get("mom_growth_20pct") and bid not in force_scale_up:
            force_scale_up.append(bid)
            logger.info(f"[CEO/Rules] 拡大トリガー: {bid} (前月比+{triggers.get('growth_rate_pct', 0):.1f}%)")

        # 水平展開推奨
        if triggers.get("roi_over_200pct"):
            expansion_targets[bid] = f"ROI {kpi.get('roi', 0):.0f}% / 同手法で別ニッチへ展開推奨"
            logger.info(f"[CEO/Rules] 水平展開推奨: {bid} (ROI {kpi.get('roi', 0):.0f}%)")

    return {
        "force_scale_up":    force_scale_up,
        "force_scale_down":  force_scale_down,
        "force_kill":        force_kill,
        "expansion_targets": expansion_targets,
        "trigger_details":   trigger_details,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 収益再投資配分（優先順位管理）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def update_revenue_pool(portfolio: dict, kpis: list, rule_triggers: dict) -> dict:
    """
    収益再投資の優先順位に従って revenue_pool を更新する。
    1位: 既存事業の運転コスト
    2位: 成長事業への追加投資
    3位: 新事業の立ち上げコスト（reserved）
    4位: オーナー報告残高（available_budget）
    """
    revenue_pool = portfolio.setdefault("revenue_pool", {})

    total_revenue    = sum(k.get("monthly_revenue", 0) for k in kpis)
    total_cost       = sum(k.get("monthly_cost", 0) for k in kpis)
    total_earned_prev = float(revenue_pool.get("total_earned", 0))

    # 累積収益を更新
    revenue_pool["total_earned"] = round(total_earned_prev + total_revenue, 0)

    # 優先1: 運転コスト（自動で差し引き済みとして計算）
    after_opex = total_revenue - total_cost

    # 優先2: 成長事業への追加投資（force_scale_up 事業のコストを1.5倍として差引）
    scale_up_ids = rule_triggers.get("force_scale_up", [])
    scale_up_extra = sum(
        k.get("monthly_cost", 0) * (SCALE_UP_BUDGET_MULT - 1)
        for k in kpis if k["id"] in scale_up_ids
    )
    after_growth = after_opex - scale_up_extra

    # 優先3: 新事業準備金（既存事業があり黒字の場合に月1000円を積立）
    active_count = sum(1 for k in kpis if k.get("status") in ("active", "reduced"))
    new_biz_reserve = 1000.0 if (after_growth > 1000 and active_count < MAX_BUSINESSES) else 0.0
    after_reserve = after_growth - new_biz_reserve

    # 優先4: オーナー残高
    available = max(0.0, after_reserve)
    prev_available = float(revenue_pool.get("available_budget", 0))
    revenue_pool["available_budget"] = round(prev_available + available, 0)

    revenue_pool["last_updated"] = str(date.today())
    revenue_pool["breakdown"] = {
        "operating_cost": round(total_cost, 0),
        "growth_investment": round(scale_up_extra, 0),
        "new_biz_reserve": round(new_biz_reserve, 0),
        "owner_balance": round(available, 0),
    }

    logger.info(
        f"[CEO] 再投資配分 → 運転:{total_cost:.0f}円 / 成長:{scale_up_extra:.0f}円 "
        f"/ 新事業積立:{new_biz_reserve:.0f}円 / 残高:{available:.0f}円"
    )
    return portfolio


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 目標KPI進捗 & プロンプト文脈生成
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_target_context(portfolio: dict, kpis: list) -> str:
    """帝国全体の目標KPI達成状況テキストを生成する"""
    businesses = portfolio.get("businesses", [])
    start_dates = [b.get("started_date", "") for b in businesses if b.get("started_date")]
    months_elapsed = 0
    if start_dates:
        start = date.fromisoformat(min(start_dates))
        months_elapsed = (date.today().year - start.year) * 12 + (date.today().month - start.month)

    total_rev = sum(k.get("monthly_revenue", 0) for k in kpis)
    lines = [f"経過: {months_elapsed}ヶ月 / 現在の月収合計: {total_rev:,.0f}円"]

    for t in REVENUE_TARGETS:
        gap = t["revenue"] - total_rev
        pct = round(total_rev / t["revenue"] * 100, 1) if t["revenue"] else 0
        status = "✅達成" if gap <= 0 else f"残り{gap:,.0f}円"
        lines.append(f"  {t['months']}ヶ月目標({t['revenue']:,}円): {status} ({pct}%達成)")

    lines.append(f"稼働事業数: {len([k for k in kpis if k.get('status') in ('active','reduced')])}/{MAX_BUSINESSES}件")
    return "\n".join(lines)


def _build_trigger_context(rule_triggers: dict, kpis: list) -> str:
    """ルールトリガーの分析結果を人間可読テキストで返す"""
    lines = []
    kpi_map = {k["id"]: k for k in kpis}
    details = rule_triggers.get("trigger_details", {})

    for bid, flags in details.items():
        kpi = kpi_map.get(bid, {})
        row = [f"{bid} (ROI:{kpi.get('roi',0):.0f}% / 月収:{kpi.get('monthly_revenue',0):,.0f}円)"]

        if flags.get("consecutive_growth_3m"):
            row.append("  🚀 3ヶ月連続増加 → 【最優先強化】")
        elif flags.get("mom_growth_20pct"):
            row.append(f"  📈 前月比+{flags.get('growth_rate_pct',0):.0f}% → 予算1.5倍推奨")

        if flags.get("roi_over_200pct"):
            row.append(f"  💰 ROI200%超 → 水平展開推奨")

        if flags.get("cost_over_revenue_2m"):
            row.append("  🔴 コスト>収益 2ヶ月連続 → 【即時停止候補】")
        elif flags.get("low_roi_3m"):
            row.append("  🔴 ROI<50% 3ヶ月連続 → 【停止候補】")
        elif flags.get("low_roi_2m"):
            row.append("  🟡 ROI<50% 2ヶ月連続 → 予算半減推奨")

        if flags.get("history_months", 0) < 2:
            row.append(f"  ℹ️  履歴{flags.get('history_months',0)}ヶ月（トリガー未評価）")

        lines.append("\n".join(row))

    forced_up   = rule_triggers.get("force_scale_up", [])
    forced_down = rule_triggers.get("force_scale_down", [])
    forced_kill = rule_triggers.get("force_kill", [])

    if forced_up:
        lines.append(f"\n【ルール確定: 拡大】 {', '.join(forced_up)}")
    if forced_down:
        lines.append(f"【ルール確定: 縮小】 {', '.join(forced_down)}")
    if forced_kill:
        lines.append(f"【ルール確定: 停止】 {', '.join(forced_kill)}")

    return "\n".join(lines) if lines else "（分析対象事業なし）"


def _build_reinvestment_context(portfolio: dict) -> str:
    """再投資配分の内訳テキストを返す"""
    bd = portfolio.get("revenue_pool", {}).get("breakdown", {})
    if not bd:
        return "（今月データなし）"
    return (
        f"1位（運転コスト）: {bd.get('operating_cost',0):,.0f}円\n"
        f"2位（成長投資）:   {bd.get('growth_investment',0):,.0f}円\n"
        f"3位（新事業積立）: {bd.get('new_biz_reserve',0):,.0f}円\n"
        f"4位（オーナー残高）: {bd.get('owner_balance',0):,.0f}円"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer2: Claude 判断（強化プロンプト）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_decision(kpis: list, history: list, portfolio: dict,
                  rule_triggers: dict, client: anthropic.Anthropic, model: str) -> dict:
    """
    ルール分析の結果を文脈として渡した上でClaudeに最終判断を求める。
    JSONの resource_allocation は force_scale_up の事業を1.5倍にした値を起点とする。
    """
    portfolio_data = json.dumps(kpis, ensure_ascii=False, indent=2)
    target_ctx     = _build_target_context(portfolio, kpis)
    trigger_ctx    = _build_trigger_context(rule_triggers, kpis)
    reinvest_ctx   = _build_reinvestment_context(portfolio)

    expansion_hint = ""
    if rule_triggers.get("expansion_targets"):
        pairs = [f"{bid}: {reason}" for bid, reason in rule_triggers["expansion_targets"].items()]
        expansion_hint = '\n  "horizontal_expansion": {"元事業名": "推奨ニッチ"},  // ROI200%超のとき'
        expansion_hint_note = "\n【水平展開候補】\n" + "\n".join(pairs)
    else:
        expansion_hint_note = ""

    prompt = f"""[目標KPI進捗]
{target_ctx}

[自動トリガー分析（ルール確定済みアクションを含む）]
{trigger_ctx}
{expansion_hint_note}
[収益再投資配分（優先順位）]
{reinvest_ctx}

以下は各事業の直近30日のデータです: {portfolio_data}
あなたはCFOです。上記の分析結果とトリガーを尊重しながら、以下をJSON形式で回答してください:

【判断基準（厳守）】
- 月収前月比+{SCALE_UP_GROWTH_PCT:.0f}%以上 → resource_allocationを{SCALE_UP_BUDGET_MULT}倍に設定
- 3ヶ月連続増加 → scale_upに追加して最優先リソース配分
- ROI{ROI_HIGH_THRESHOLD:.0f}%超 → scale_upに追加し、horizontal_expansionに別ニッチを提示
- ROI{ROI_LOW_THRESHOLD:.0f}%未満2ヶ月連続 → scale_downに追加して予算半減
- ROI{ROI_LOW_THRESHOLD:.0f}%未満3ヶ月連続 → killに追加
- コスト>収益2ヶ月連続 → killに追加（即時停止候補）
- ルール確定済みのアクションは必ず反映すること
- 最大事業数{MAX_BUSINESSES}件厳守
{{
  "resource_allocation": {{"事業名": 予算割合(%)}},{expansion_hint}
  "scale_up": ["拡大すべき事業名"],
  "scale_down": ["縮小すべき事業名"],
  "kill": ["停止すべき事業名"],
  "reasoning": "判断理由（日本語200字以内）"
}}"""

    response = client.messages.create(
        model=model,
        max_tokens=1536,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.content[0].text
    record_empire_cost("ceo", response.usage.input_tokens, response.usage.output_tokens)

    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            raise ValueError(f"JSONを抽出できませんでした: {content[:300]}")
        json_str = json_match.group(0)

    decision = json.loads(json_str)

    # ── ハードルール強制マージ ──────────────────────────────────────
    # ClaudeがルールトリガーをJSONに反映し忘れた場合のフォールバック
    for bid in rule_triggers.get("force_scale_up", []):
        if bid not in decision.get("scale_up", []):
            decision.setdefault("scale_up", []).append(bid)
            logger.debug(f"[CEO/Merge] force_scale_up 強制追加: {bid}")

    for bid in rule_triggers.get("force_scale_down", []):
        if bid not in decision.get("scale_down", []) and bid not in decision.get("kill", []):
            decision.setdefault("scale_down", []).append(bid)
            logger.debug(f"[CEO/Merge] force_scale_down 強制追加: {bid}")

    for bid in rule_triggers.get("force_kill", []):
        if bid not in decision.get("kill", []):
            decision.setdefault("kill", []).append(bid)
            if bid in decision.get("scale_up", []):
                decision["scale_up"].remove(bid)
            logger.debug(f"[CEO/Merge] force_kill 強制追加: {bid}")

    return decision


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 判断の適用
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def apply_decision(portfolio: dict, decision: dict, kpis: list,
                   rule_triggers: dict, notify_fn=None) -> dict:
    """CEO判断を portfolio.yaml に反映する（kill は24時間後）"""
    businesses   = portfolio.get("businesses", [])
    allocation   = decision.get("resource_allocation", {})
    scale_up     = decision.get("scale_up", [])
    scale_down   = decision.get("scale_down", [])
    kill_list    = decision.get("kill", [])
    kpi_map      = {k["id"]: k for k in kpis}

    for b in businesses:
        bid = b["id"]
        kpi = kpi_map.get(bid, {})
        triggers = rule_triggers.get("trigger_details", {}).get(bid, {})

        # 予算配分
        if bid in allocation:
            base_weight = float(allocation[bid]) / 100.0
            # force_scale_up は1.5倍を確実に適用
            if bid in rule_triggers.get("force_scale_up", []):
                base_weight = min(base_weight * SCALE_UP_BUDGET_MULT, 1.0)
            b["resource_weight"] = round(base_weight, 2)

        # 拡大
        if bid in scale_up and b.get("status") in ("reduced", "paused", "paused_cost_limit"):
            b["status"] = "active"
            logger.info(f"[CEO] 拡大適用: {bid}")

        # 縮小
        if bid in scale_down and b.get("status") == "active":
            b["status"] = "reduced"
            logger.info(f"[CEO] 縮小適用: {bid}")

        # 停止（24時間後・Slack通知）
        if bid in kill_list and b.get("status") not in ("pending_kill", "terminated"):
            kill_at = (datetime.now() + timedelta(hours=24)).isoformat()
            pending = portfolio.setdefault("safety", {}).setdefault("pending_kills", [])
            pending.append({
                "business_id": bid,
                "scheduled_at": datetime.now().isoformat(),
                "kill_at": kill_at,
                "reason": decision.get("reasoning", "")[:100],
            })
            b["status"] = "pending_kill"
            msg = (
                f"⚠️ *[CEO判断]* 事業「{bid}」の停止を決定\n"
                f"理由: {decision.get('reasoning', '')[:80]}\n"
                f"24時間後（{kill_at[:16]}）に実行されます。"
            )
            logger.warning(msg)
            if notify_fn:
                notify_fn("帝国通知", msg)

    # 水平展開推奨を empire_kpi に記録（launcher_agent が参照）
    if decision.get("horizontal_expansion"):
        portfolio.setdefault("empire_kpi", {})["horizontal_expansion_pending"] = decision["horizontal_expansion"]
        logger.info(f"[CEO] 水平展開推奨を記録: {decision['horizontal_expansion']}")

    return portfolio


def process_pending_kills(portfolio: dict, notify_fn=None) -> tuple:
    """24時間経過した停止予定事業を実際に停止する"""
    now = datetime.now()
    pending     = portfolio.get("safety", {}).get("pending_kills", [])
    still, done = [], []

    for pk in pending:
        try:
            kill_at = datetime.fromisoformat(pk["kill_at"])
        except (ValueError, KeyError):
            still.append(pk)
            continue

        if now >= kill_at:
            bid = pk["business_id"]
            for b in portfolio.get("businesses", []):
                if b["id"] == bid:
                    b["status"] = "terminated"
            done.append(bid)
            msg = f"🔴 *[帝国]* 事業「{bid}」を停止しました。"
            logger.info(msg)
            if notify_fn:
                notify_fn("帝国通知", msg)
        else:
            still.append(pk)

    portfolio["safety"]["pending_kills"] = still
    return portfolio, done


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KPI → portfolio 同期
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def sync_kpis_to_portfolio(portfolio: dict, kpis: list) -> dict:
    kpi_map = {k["id"]: k for k in kpis}
    for b in portfolio.get("businesses", []):
        if b["id"] in kpi_map:
            k = kpi_map[b["id"]]
            b["monthly_revenue"] = k.get("monthly_revenue", 0)
            b["monthly_cost"]    = k.get("monthly_cost", 0)
            b["roi"]             = k.get("roi", 0)

    active = [k for k in kpis if k.get("status") in ("active", "reduced")]
    best   = max(active, key=lambda k: k.get("monthly_revenue", 0), default=None)

    empire_kpi = portfolio.setdefault("empire_kpi", {})
    empire_kpi["total_monthly_revenue"] = sum(k.get("monthly_revenue", 0) for k in kpis)
    empire_kpi["total_monthly_cost"]    = sum(k.get("monthly_cost", 0) for k in kpis)
    empire_kpi["business_count"]        = len(active)
    empire_kpi["best_performer"]        = best["id"] if best else ""
    empire_kpi["last_updated"]          = str(date.today())
    return portfolio


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ログ・週次レポート
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def log_decision(kpis: list, decision: dict, rule_triggers: dict):
    CEO_DECISIONS_LOG.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "date": str(date.today()),
        "kpi_snapshot": kpis,
        "rule_triggers": {
            "force_scale_up":    rule_triggers.get("force_scale_up", []),
            "force_scale_down":  rule_triggers.get("force_scale_down", []),
            "force_kill":        rule_triggers.get("force_kill", []),
            "expansion_targets": rule_triggers.get("expansion_targets", {}),
        },
        "decision": decision,
    }
    with CEO_DECISIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send_weekly_report(portfolio: dict, notify_fn=None):
    businesses = portfolio.get("businesses", [])
    active     = [b for b in businesses if b.get("status") in ("active", "reduced")]
    kpi        = portfolio.get("empire_kpi", {})
    revenue_pool = portfolio.get("revenue_pool", {})

    biz_lines = "\n".join(
        f"  • {b['id']}: 収益{b.get('monthly_revenue',0):,.0f}円 / "
        f"コスト{b.get('monthly_cost',0):,.0f}円 / ROI{b.get('roi',0):.0f}%"
        for b in active
    ) or "  稼働中の事業なし"

    total_rev  = float(kpi.get("total_monthly_revenue", 0))
    total_cost = float(kpi.get("total_monthly_cost", 0))
    total_profit = total_rev - total_cost
    roi_overall = (total_profit / total_cost * 100) if total_cost > 0 else 0.0

    next_target = next((t for t in REVENUE_TARGETS if t["revenue"] > total_rev), None)
    target_line = (
        f"次の目標({next_target['revenue']:,}円)まで残り{next_target['revenue']-total_rev:,}円"
        if next_target else "全目標達成！"
    )

    pages_url = "https://blackINT400.github.io/note-auto-revenue/"

    msg = (
        f"📊 *週次帝国レポート* ({date.today()})\n\n"
        f"💰 今月の収益: ¥{total_rev:,.0f} / コスト: ¥{total_cost:,.0f} / "
        f"純利益: ¥{total_profit:,.0f} (ROI {roi_overall:.1f}%)\n"
        f"🏢 稼働事業: {kpi.get('business_count',0)}/{MAX_BUSINESSES}件\n"
        f"🏦 累積収益プール: ¥{revenue_pool.get('total_earned',0):,.0f} / "
        f"残高: ¥{revenue_pool.get('available_budget',0):,.0f}\n"
        f"🎯 {target_line}\n\n"
        f"【事業別】\n{biz_lines}\n\n"
        f"📈 ダッシュボード: {pages_url}"
    )
    logger.info(f"週次レポート\n{msg}")
    if notify_fn:
        notify_fn("帝国通知", msg)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メイン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run(notify_fn=None, weekly_report: bool = False) -> dict:
    portfolio  = load_portfolio()
    businesses = portfolio.get("businesses", [])

    # 安全装置: 全事業コスト合計チェック
    cost_limit = get_empire_cost_limit()
    total_cost = sum(float(b.get("monthly_cost", 0)) for b in businesses)
    if total_cost >= cost_limit:
        msg = f"🛑 *[帝国]* 月間コスト上限（{cost_limit:,.0f}円）到達。全事業を一時停止します。"
        logger.error(msg)
        if notify_fn:
            notify_fn("帝国通知", msg)
        for b in businesses:
            if b.get("status") == "active":
                b["status"] = "paused_cost_limit"
        save_portfolio(portfolio)
        return portfolio

    # KPI収集 → portfolio同期
    logger.info("[CEO] KPI収集...")
    kpis      = collect_all_kpis(businesses)
    portfolio = sync_kpis_to_portfolio(portfolio, kpis)

    # 月次スナップショット保存（履歴管理）
    _save_monthly_snapshot(kpis)
    history = _load_kpi_history()

    # Layer1: ハードルール適用
    logger.info("[CEO] ハードルール判定...")
    rule_triggers = apply_rule_triggers(kpis, history)

    # 収益再投資配分
    portfolio = update_revenue_pool(portfolio, kpis, rule_triggers)

    # 停止予定処理
    portfolio, executed = process_pending_kills(portfolio, notify_fn)
    if executed:
        logger.info(f"[CEO] 停止実行完了: {executed}")

    # Layer2: Claude判断（コンテキスト付き）
    if kpis:
        try:
            client   = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            model    = get_model()
            decision = make_decision(kpis, history, portfolio, rule_triggers, client, model)
            log_decision(kpis, decision, rule_triggers)
            portfolio = apply_decision(portfolio, decision, kpis, rule_triggers, notify_fn)
            logger.info(f"[CEO] 判断完了: {decision.get('reasoning', '')[:80]}")
        except Exception as e:
            logger.error(f"[CEO] Claude API失敗: {e}")
            log_decision(kpis, {"error": str(e)}, rule_triggers)

    save_portfolio(portfolio)

    if weekly_report:
        send_weekly_report(portfolio, notify_fn)

    logger.info("[CEO] 処理完了")
    return portfolio
