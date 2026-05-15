"""
empire/market_analyst.py — 自律市場分析エージェント

週次で副業市場全体を自律的に分析:
  STEP1: 市場データ収集（はてなブックマーク・Reddit・Google Trends・Product Hunt）
  STEP2: Claude API で仮説生成・推奨導出（オーナー思考OS注入）
  STEP3: 分析結果を owner/market_learnings.json に蓄積・カテゴリ重み自動更新
  STEP4: 前週仮説の検証と自己学習ループ
  STEP5: 月次のみ owner/forecast.md を更新（3ヶ月後への手紙）
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

import anthropic
import requests
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_PATH = PROJECT_ROOT / "empire" / "portfolio.yaml"
OWNER_THOUGHTS_PATH = PROJECT_ROOT / "owner" / "thoughts.md"
OWNER_LEARNINGS_PATH = PROJECT_ROOT / "owner" / "learnings.md"
MARKET_LEARNINGS_PATH = PROJECT_ROOT / "owner" / "market_learnings.json"
FORECAST_PATH = PROJECT_ROOT / "owner" / "forecast.md"

CATEGORIES = [
    "コンテンツ販売（note・Zenn・ブログ）",
    "デジタル商品（テンプレ・ツール・PDF）",
    "アフィリエイト・SEO",
    "APIサービス・SaaS",
    "物販（転売・せどり・Poizon等）",
    "動画・音声コンテンツ",
    "コンサル・スキル販売",
    "自動化ツール販売",
    "NFT・デジタルアセット",
    "新興プラットフォーム（新しい収益化手段）",
]

_HEADERS = {"User-Agent": "EmpireMarketBot/1.0 (auto-analysis)"}


# ── データロード ───────────────────────────────────────────────────────────────

def _load_portfolio() -> dict:
    try:
        return yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_owner_thoughts() -> str:
    try:
        return OWNER_THOUGHTS_PATH.read_text(encoding="utf-8")[:3000]
    except Exception:
        return ""


def _load_learnings() -> dict:
    default: dict = {
        "weeks": [],
        "category_weights": {c: 1.0 for c in CATEGORIES},
        "overall_accuracy": 0.0,
    }
    if not MARKET_LEARNINGS_PATH.exists():
        return default
    try:
        data = json.loads(MARKET_LEARNINGS_PATH.read_text(encoding="utf-8"))
        for cat in CATEGORIES:
            data.setdefault("category_weights", {})[cat] = data["category_weights"].get(cat, 1.0)
        return data
    except Exception:
        return default


def _save_learnings(data: dict) -> None:
    MARKET_LEARNINGS_PATH.parent.mkdir(exist_ok=True)
    MARKET_LEARNINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── STEP1: 市場データ収集 ─────────────────────────────────────────────────────

def _fetch_hatena() -> str:
    """はてなブックマーク IT・経済 ホットエントリ"""
    titles: list[str] = []
    for tag in ("it", "economics"):
        try:
            r = requests.get(f"https://b.hatena.ne.jp/hotentry/{tag}.json",
                             headers=_HEADERS, timeout=10)
            if r.status_code == 200:
                for entry in r.json()[:15]:
                    t = entry.get("title", "").strip()
                    if t:
                        titles.append(t)
        except Exception as e:
            logger.warning("はてなブックマーク取得失敗 (%s): %s", tag, e)
    return "\n".join(titles[:25]) if titles else "取得失敗"


def _fetch_reddit(subreddit: str, limit: int = 10) -> str:
    """Reddit ホットポスト（認証不要の公開JSON）"""
    try:
        r = requests.get(
            f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}",
            headers=_HEADERS, timeout=10,
        )
        if r.status_code == 200:
            posts = r.json().get("data", {}).get("children", [])
            return "\n".join(
                p["data"]["title"] for p in posts if p.get("data", {}).get("title")
            )
    except Exception as e:
        logger.warning("Reddit取得失敗 (%s): %s", subreddit, e)
    return "取得失敗"


def _fetch_google_trends(keywords: list[str]) -> str:
    """pytrends で Google Trends スコアを取得（日本・過去7日）"""
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="ja-JP", tz=540)
        results: dict[str, int] = {}
        for i in range(0, len(keywords), 5):
            batch = keywords[i:i + 5]
            try:
                pt.build_payload(batch, timeframe="now 7-d", geo="JP")
                df = pt.interest_over_time()
                if not df.empty:
                    for kw in batch:
                        if kw in df.columns:
                            results[kw] = int(df[kw].mean())
                time.sleep(2)
            except Exception:
                pass
        if results:
            return "\n".join(
                f"{k}: {v}/100"
                for k, v in sorted(results.items(), key=lambda x: x[1], reverse=True)
            )
    except ImportError:
        logger.warning("pytrends 未インストール — Google Trends スキップ")
    except Exception as e:
        logger.warning("Google Trends取得失敗: %s", e)
    return "取得失敗"


def _fetch_product_hunt() -> str:
    """Product Hunt RSS から最新プロダクト名を取得"""
    try:
        r = requests.get("https://www.producthunt.com/feed",
                         headers=_HEADERS, timeout=10)
        if r.status_code == 200:
            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", r.text)
            if not titles:
                titles = re.findall(r"<title>(.*?)</title>", r.text)
            return "\n".join(titles[1:11])  # 先頭はチャネル名なので除外
    except Exception as e:
        logger.warning("Product Hunt取得失敗: %s", e)
    return "取得失敗"


def collect_market_data() -> dict[str, str]:
    """全ソースからデータを収集して dict で返す"""
    logger.info("[市場分析] STEP1: データ収集開始")
    trend_keywords = [
        "副業", "稼ぐ方法", "note 収益化", "Zenn 稼ぐ",
        "アフィリエイト", "デジタル商品", "自動化 収益",
    ]
    return {
        "hatena": _fetch_hatena(),
        "reddit_passive": _fetch_reddit("passive_income"),
        "reddit_entrepreneur": _fetch_reddit("entrepreneur"),
        "google_trends": _fetch_google_trends(trend_keywords),
        "product_hunt": _fetch_product_hunt(),
    }


# ── STEP2: Claude API で自律分析 ─────────────────────────────────────────────

def _build_analysis_prompt(
    market_data: dict[str, str],
    portfolio: dict,
    owner_thoughts: str,
    learnings: dict,
) -> str:
    biz_summary = "\n".join(
        f"- {b['id']} ({b.get('status', '?')}): 月収 ¥{b.get('monthly_revenue', 0):,}"
        for b in portfolio.get("businesses", [])
    ) or "（事業なし）"

    weight_str = "\n".join(
        f"- {k}: {v:.1f}" for k, v in learnings.get("category_weights", {}).items()
    )

    last_week = learnings.get("weeks", [])[-1] if learnings.get("weeks") else None
    prev_hypothesis = last_week.get("hypothesis", "なし（初回）") if last_week else "なし（初回）"
    prev_recommendation = last_week.get("recommendation", "なし（初回）") if last_week else "なし（初回）"

    market_str = "\n\n".join([
        f"【はてなブックマーク IT/経済】\n{market_data.get('hatena', '取得失敗')}",
        f"【Reddit r/passive_income】\n{market_data.get('reddit_passive', '取得失敗')}",
        f"【Reddit r/entrepreneur】\n{market_data.get('reddit_entrepreneur', '取得失敗')}",
        f"【Google Trends（日本 過去7日）】\n{market_data.get('google_trends', '取得失敗')}",
        f"【Product Hunt 最新プロダクト】\n{market_data.get('product_hunt', '取得失敗')}",
    ])

    categories_json = json.dumps(CATEGORIES, ensure_ascii=False)

    return f"""あなたは優秀なビジネスアナリストです。以下の市場データを分析してください。

== 市場データ ==
{market_str}

== オーナーの現状 ==
{biz_summary}

== オーナーの思考・価値観（最優先で参照）==
{owner_thoughts[:2500]}

== カテゴリ分析重み（過去の学習による補正）==
{weight_str}

== 先週の仮説・推奨 ==
仮説: {prev_hypothesis}
推奨: {prev_recommendation}

## 分析カテゴリ
{categories_json}

## 出力形式

JSONのみ出力してください（説明・前置き不要）:

{{
  "hot_markets": [
    {{
      "category": "カテゴリ名（上記リストから選択）",
      "opportunity_score": 1から100の整数,
      "reason": "なぜ今熱いか（構造的に説明、100字以内）",
      "competition": "low または medium または high",
      "automation_level": "完全自動 または 半自動 または 手動",
      "estimated_monthly_revenue": "X〜Y万円",
      "time_to_first_revenue": "X週間",
      "fit_with_owner": "オーナーの思考・現状との相性（50字以内）",
      "required_investment": "X円",
      "risk_level": "low または medium または high"
    }}
  ],
  "declining_markets": [
    {{
      "category": "カテゴリ名",
      "reason": "なぜ下火か（50字以内）"
    }}
  ],
  "hypothesis": "今週の市場から導いた独自の仮説（1つ、100字以内）",
  "recommendation": "今のオーナーに最も合う次の一手（100字以内）",
  "reasoning": "ミリテク思考OS（0→1の言語化）で構造的に説明（200字以内）",
  "hypothesis_verification": {{
    "last_hypothesis": "{prev_hypothesis}",
    "result": "confirmed または rejected または insufficient_data",
    "reason": "検証結果の理由（50字以内）",
    "learning": "次に活かすこと（50字以内）"
  }},
  "relative_axis": {{
    "saturation": "今の時代で飽和しているもの（50字以内）",
    "desire": "その対極にある渇望（50字以内）",
    "next_structure": "次に来る市場構造の予測（100字以内）"
  }},
  "abstraction": {{
    "abstract_demand": "本質的な需要構造（抽象レベル、80字以内）",
    "concrete_application": "今の市場での具体化・ビジネス案（80字以内）",
    "existing_business_use": "オーナーの既存事業への応用方法（80字以内）"
  }},
  "copied_structure": {{
    "reference_case": "参照した成功しているビジネス・コンテンツ名",
    "abstracted_essence": "そこから抽象化した本質的な需要構造（80字以内）",
    "translated_form": "自分のコンテンツ・ビジネスへの落とし込み（80字以内）"
  }},
  "strategic_reasoning": "今週の戦略判断の理由をミリテク思考OS（相対軸・抽象→具体・全1構造）で説明（200字以内）"
}}

hot_marketsは上位5カテゴリのみ。opportunity_scoreはカテゴリ重みを考慮して補正すること。"""


def analyze_with_claude(
    market_data: dict[str, str],
    portfolio: dict,
    owner_thoughts: str,
    learnings: dict,
    client: anthropic.Anthropic,
    model: str,
) -> tuple[dict, int, int]:
    """Claude API で分析して (analysis_dict, input_tokens, output_tokens) を返す"""
    logger.info("[市場分析] STEP2: Claude API で分析中...")
    prompt = _build_analysis_prompt(market_data, portfolio, owner_thoughts, learnings)

    from empire.utils import get_master_os
    master_os = get_master_os()

    kwargs: dict = {
        "model": model,
        "max_tokens": 3500,
        "messages": [{"role": "user", "content": prompt}],
    }
    if master_os:
        kwargs["system"] = master_os

    response = client.messages.create(**kwargs)
    text = response.content[0].text.strip()

    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not json_match:
        raise ValueError(f"JSON抽出失敗: {text[:300]}")

    return json.loads(json_match.group()), response.usage.input_tokens, response.usage.output_tokens


# ── STEP3: 学習データ保存・カテゴリ重み更新 ──────────────────────────────────

def save_learning(analysis: dict, learnings: dict) -> dict:
    """分析結果を market_learnings.json に追記し、カテゴリ重みを自動更新"""
    logger.info("[市場分析] STEP3: 学習データ保存")

    week_record = {
        "week": str(date.today()),
        "hot_markets": analysis.get("hot_markets", []),
        "declining_markets": analysis.get("declining_markets", []),
        "hypothesis": analysis.get("hypothesis", ""),
        "recommendation": analysis.get("recommendation", ""),
        "reasoning": analysis.get("reasoning", ""),
        "hypothesis_verification": analysis.get("hypothesis_verification", {}),
    }
    learnings.setdefault("weeks", []).append(week_record)
    learnings["weeks"] = learnings["weeks"][-52:]  # 1年分のみ保持

    # カテゴリ重み自動更新
    verification = analysis.get("hypothesis_verification", {})
    result = verification.get("result", "insufficient_data")
    hot_categories = [m.get("category", "") for m in analysis.get("hot_markets", [])[:3]]

    for cat in hot_categories:
        if cat not in learnings["category_weights"]:
            continue
        if result == "confirmed":
            learnings["category_weights"][cat] = min(2.0, learnings["category_weights"][cat] + 0.1)
        elif result == "rejected":
            learnings["category_weights"][cat] = max(0.5, learnings["category_weights"][cat] - 0.05)

    # 全体精度スコア更新
    verified = [
        w for w in learnings["weeks"]
        if w.get("hypothesis_verification", {}).get("result") in ("confirmed", "rejected")
    ]
    if verified:
        confirmed_count = sum(
            1 for w in verified if w["hypothesis_verification"]["result"] == "confirmed"
        )
        learnings["overall_accuracy"] = round(confirmed_count / len(verified) * 100, 1)

    _save_learnings(learnings)
    logger.info("[市場分析] 学習完了: 精度 %s%%", learnings["overall_accuracy"])
    return learnings


# ── STEP5: 月次フォーキャスト ─────────────────────────────────────────────────

def generate_forecast(
    portfolio: dict,
    learnings: dict,
    client: anthropic.Anthropic,
    model: str,
) -> None:
    """月初のみ: 3ヶ月後への手紙を owner/forecast.md に更新"""
    if date.today().day != 1:
        return

    logger.info("[市場分析] 月次フォーキャスト生成中...")
    current_revenue = float(portfolio.get("empire_kpi", {}).get("total_monthly_revenue", 0))
    recent_hot = []
    for w in learnings.get("weeks", [])[-4:]:
        for m in w.get("hot_markets", [])[:3]:
            cat = m.get("category", "")
            if cat and cat not in recent_hot:
                recent_hot.append(cat)

    prompt = f"""現在の事業状況と市場分析をもとに、3ヶ月後の予測を書いてください。

現在の月収: ¥{current_revenue:,.0f}
直近の注目市場: {', '.join(recent_hot[:5]) or 'なし'}
市場予測精度: {learnings.get('overall_accuracy', 0)}%

3ヶ月後のオーナーへの手紙として以下を書いてください:
1. 現在の軌跡が続いた場合の月収予測（楽観・中央値・悲観の3パターン）
2. 最大のリスク要因（1つ）
3. 最大の成長機会（1つ）
4. ミリテク思考OS（0→1の構造）で見たとき、今の選択が正しい理由

500字以内で書いてください。"""

    try:
        response = client.messages.create(
            model=model, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        FORECAST_PATH.write_text(
            f"# 3ヶ月後への手紙\n\n生成日: {date.today()}\n\n{text}\n\n"
            f"---\n*毎月1日に自動更新されます*\n",
            encoding="utf-8",
        )
        logger.info("[市場分析] forecast.md 更新完了")
    except Exception as e:
        logger.error("[市場分析] フォーキャスト生成失敗: %s", e)


# ── Discord 週次レポート用フォーマット ────────────────────────────────────────

def format_discord_section(analysis: dict, learnings: dict) -> str:
    """Discord 週次レポート用マーケット分析テキストを返す（思考プロセス全掲載・自分だけが見る）"""
    hot = analysis.get("hot_markets", [])[:3]
    ra = analysis.get("relative_axis", {})
    ab = analysis.get("abstraction", {})
    cp = analysis.get("copied_structure", {})
    verification = analysis.get("hypothesis_verification", {})
    accuracy = learnings.get("overall_accuracy", 0)

    verify_label = {
        "confirmed": "✅ 正しかった",
        "rejected": "❌ 外れた",
        "insufficient_data": "⚠️ データ不足",
    }.get(verification.get("result", ""), "⚠️ 初回のため検証なし")

    lines = [
        "━━━━━━━━━━━━━━━",
        "🧠 今週の思考プロセス",
        "━━━━━━━━━━━━━━━",
        "",
        "**【市場の相対軸】**",
        f"今飽和しているもの: {ra.get('saturation', '—')}",
        f"その対極の渇望: {ra.get('desire', '—')}",
        f"次に来る構造: {ra.get('next_structure', '—')}",
        "",
        "**【抽象→具体の翻訳】**",
        f"抽象構造: {ab.get('abstract_demand', '—')}",
        f"今の市場での具体化: {ab.get('concrete_application', '—')}",
        f"既存事業への応用: {ab.get('existing_business_use', '—')}",
        "",
        "**【今週パクった構造】**",
        f"参照した成功事例: {cp.get('reference_case', '—')}",
        f"抽象化した本質: {cp.get('abstracted_essence', '—')}",
        f"翻訳した形: {cp.get('translated_form', '—')}",
        "",
        "**【システムの判断理由】**",
        f"今週の戦略をこう決めた理由:",
        analysis.get("strategic_reasoning") or analysis.get("reasoning", "—"),
        "",
        "━━━━━━━━━━━━━━━",
        "",
        "**🎯 今週の推奨・実行**",
        "",
        f"推奨する次の一手: {analysis.get('recommendation', '—')}",
        "",
    ]

    if hot:
        lines.append("**注目市場トップ3**")
        for i, m in enumerate(hot, 1):
            lines.append(
                f"{i}位: {m.get('category', '?')}（スコア {m.get('opportunity_score', '?')}/100）"
                f"\n　月収目安: {m.get('estimated_monthly_revenue', '?')}"
                f"\n　相性: {m.get('fit_with_owner', '?')}"
            )
        lines.append("")

    lines += [
        f"今週の仮説: {analysis.get('hypothesis', '—')}",
        "",
        "**【先週の仮説の検証】**",
        f"仮説: {verification.get('last_hypothesis', '初回のため検証なし')}",
        f"結果: {verify_label}",
        f"学習: {verification.get('learning', '初回')}",
        "",
        f"📈 累計予測精度: {accuracy}%",
    ]

    return "\n".join(lines)[:4000]


# ── メインエントリーポイント ───────────────────────────────────────────────────

def run(
    client: anthropic.Anthropic | None = None,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """
    週次市場分析を実行して結果を返す。

    Returns:
        {"analysis": dict, "discord_section": str}
    """
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    portfolio = _load_portfolio()
    owner_thoughts = _load_owner_thoughts()
    learnings = _load_learnings()

    market_data = collect_market_data()
    analysis, inp, out = analyze_with_claude(
        market_data, portfolio, owner_thoughts, learnings, client, model
    )

    try:
        from empire.utils import record_empire_cost
        record_empire_cost("market_analyst", inp, out)
    except Exception:
        pass

    learnings = save_learning(analysis, learnings)
    generate_forecast(portfolio, learnings, client, model)

    # ── STEP5: 行動確信プロトコル — トップ市場の命題を証明 ─────────────────────
    logger.info("[市場分析] STEP5: 行動確信プロトコル（市場命題の証明）")
    top_market_proof: dict = {}
    try:
        from empire.proposition_lib import (
            load_propositions, find_similar,
            prove_proposition, add_proposition,
            confidence_label,
        )
        from empire.utils import get_master_os, notify

        hot = analysis.get("hot_markets", [])
        if hot:
            top = hot[0]
            proposition = f"{top.get('category', '?')}市場への参入は今週が最適タイミングである"
            observation = (
                f"スコア: {top.get('opportunity_score', 0)}/100 / "
                f"競合: {top.get('competition', '?')} / "
                f"月収目安: {top.get('estimated_monthly_revenue', '?')} / "
                f"自動化: {top.get('automation_level', '?')}"
            )
            props_data = load_propositions()
            similar = find_similar(proposition, props_data)
            master_os = get_master_os()
            proof = prove_proposition(
                client, model, proposition, observation, master_os, "市場分析", similar
            )
            record = add_proposition(props_data, proof, "市場分析", top.get("category", ""))
            top_market_proof = proof
            conf = proof.get("confidence", 0)
            logger.info(
                "[確信プロトコル] 市場証明完了: %s（確信度: %d%%）", proposition[:40], conf
            )
            # 確信度が閾値未満の場合は Discord に警告
            if conf < 50:
                notify(
                    "⚠️ [市場分析] 確信度不足",
                    f"命題: {proposition}\n全: {proof.get('universal_truth', '—')}\n"
                    f"{confidence_label(conf)}\nオーナーの確認が推奨されます。",
                )
    except Exception as e:
        logger.warning("[確信プロトコル] 市場命題の証明エラー: %s", e)

    discord_section = format_discord_section(analysis, learnings)
    logger.info("[市場分析] 完了 | 仮説: %s", analysis.get("hypothesis", "")[:60])

    return {
        "analysis": analysis,
        "discord_section": discord_section,
        "top_market_proof": top_market_proof,
    }


# ── フィードバック処理 ────────────────────────────────────────────────────────

def apply_feedback(feedback_text: str) -> str:
    """
    オーナーのフィードバックを解析して反映する。

    コマンド:
      「継続」          → 現在方向性を強化（重み +0.05）
      「修正: XXX」     → thoughts.md に分析方針として追記
      「アイデア: XXX」 → thoughts.md にアイデアとして追記
      「却下: XXX」     → 該当カテゴリの重みを大幅に下げる
    """
    feedback_text = feedback_text.strip()
    learnings = _load_learnings()
    thoughts_path = PROJECT_ROOT / "owner" / "thoughts.md"

    def _append_thoughts(content: str) -> None:
        existing = thoughts_path.read_text(encoding="utf-8") if thoughts_path.exists() else ""
        thoughts_path.write_text(existing.rstrip() + "\n" + content, encoding="utf-8")

    if feedback_text == "継続":
        last_week = learnings.get("weeks", [])[-1] if learnings.get("weeks") else None
        if last_week:
            for m in last_week.get("hot_markets", [])[:3]:
                cat = m.get("category", "")
                if cat in learnings["category_weights"]:
                    learnings["category_weights"][cat] = min(2.0, learnings["category_weights"][cat] + 0.05)
        _save_learnings(learnings)
        result = "現在の方向性を強化しました（カテゴリ重み +0.05）"

    elif feedback_text.startswith("修正:"):
        note = feedback_text[3:].strip()
        _append_thoughts(
            f"\n## {date.today()} フィードバック（修正）\n"
            f"- 市場分析方針の修正: {note}\n"
        )
        result = f"thoughts.md に修正方針を追記しました: {note}"

    elif feedback_text.startswith("アイデア:"):
        idea = feedback_text[5:].strip()
        _append_thoughts(
            f"\n## {date.today()} アイデアメモ（フィードバック）\n"
            f"- {idea}\n"
        )
        result = f"thoughts.md にアイデアを追記しました: {idea}"

    elif feedback_text.startswith("却下:"):
        target = feedback_text[3:].strip()
        updated = []
        for cat in learnings["category_weights"]:
            if target in cat:
                learnings["category_weights"][cat] = max(0.3, learnings["category_weights"][cat] - 0.3)
                updated.append(cat)
        _save_learnings(learnings)
        result = f"却下: {updated} の重みを下げました" if updated else f"「{target}」に一致するカテゴリが見つかりませんでした"

    else:
        result = f"不明なフィードバックコマンドです: {feedback_text[:50]}"

    logger.info("[フィードバック] %s", result)
    return result
