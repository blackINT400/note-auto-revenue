"""
analyst.py: 収益分析・戦略改善エージェント

A. A/Bテスト   : タイトル2案の勝者パターンを学習 → data/learnings.json
B. 構成最適化  : 品質スコア分析 → quality_threshold を ±5 自動調整
               （Zennは無料のため、note/WP移行時の価格最適化ロジックに相当）
C. テーマ発掘  : 投稿パターンから読者ニーズを抽出 → top_keywords に追加
D. 月次サマリー: 毎月1日に logs/monthly_YYYY-MM.md を生成（main.pyから呼び出し）
"""
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import anthropic
import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")
PUBLISHED_LOG_PATH = Path("logs/published.jsonl")
COST_TRACKER_PATH = Path("data/cost_tracker.json")
AB_VARIANTS_PATH = Path("data/ab_variants.json")
LEARNINGS_PATH = Path("data/learnings.json")
MONTHLY_LOG_DIR = Path("logs")

INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0
JPY_RATE = 150.0


# ── 共通ユーティリティ ────────────────────────────────────────────────────────

def _load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _save_config(config: dict):
    CONFIG_PATH.write_text(
        yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _get_month_cost() -> float:
    if not COST_TRACKER_PATH.exists():
        return 0.0
    tracker = json.loads(COST_TRACKER_PATH.read_text(encoding="utf-8"))
    if tracker.get("month") != str(date.today())[:7]:
        return 0.0
    return tracker.get("total_jpy", 0.0)


def _record_cost(input_tokens: int, output_tokens: int):
    usd = (input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
           + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK)
    jpy = usd * JPY_RATE
    current_month = str(date.today())[:7]
    COST_TRACKER_PATH.parent.mkdir(exist_ok=True)
    if COST_TRACKER_PATH.exists():
        tracker = json.loads(COST_TRACKER_PATH.read_text(encoding="utf-8"))
        if tracker.get("month") != current_month:
            tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}
    else:
        tracker = {"month": current_month, "total_jpy": 0.0, "calls": []}
    tracker["total_jpy"] = round(tracker["total_jpy"] + jpy, 2)
    tracker["calls"].append({"date": str(date.today()), "cost_jpy": round(jpy, 2)})
    COST_TRACKER_PATH.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_articles() -> list:
    if not PUBLISHED_LOG_PATH.exists():
        return []
    articles = []
    for line in PUBLISHED_LOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                articles.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return articles


def _load_learnings() -> dict:
    default = {"ab_results": [], "winning_patterns": {}, "theme_insights": []}
    if not LEARNINGS_PATH.exists():
        return default
    try:
        return json.loads(LEARNINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_learnings(data: dict):
    LEARNINGS_PATH.parent.mkdir(exist_ok=True)
    LEARNINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_master_os() -> str:
    path = Path(__file__).parent.parent / "owner" / "context_prompt.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _call_claude(client: anthropic.Anthropic, prompt: str, model: str,
                 max_tokens: int = 1024) -> tuple:
    """(テキスト, input_tokens, output_tokens)"""
    master_os = _load_master_os()
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if master_os:
        kwargs["system"] = master_os
    response = client.messages.create(**kwargs)
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


# ── A. A/Bテスト評価 ──────────────────────────────────────────────────────────

def _evaluate_ab_tests(client: anthropic.Anthropic, model: str):
    """
    未評価のA/Bバリアントを Claude で評価し learnings.json に勝者パターンを記録する。
    ZennにはPV APIがないため、タイトルの品質ヒューリスティックで代替評価する。
    """
    if not AB_VARIANTS_PATH.exists():
        logger.info("[A/Bテスト] バリアントデータなし。スキップします。")
        return

    variants: list = json.loads(AB_VARIANTS_PATH.read_text(encoding="utf-8"))
    pending = [v for v in variants if not v.get("evaluated")]
    if not pending:
        logger.info("[A/Bテスト] 未評価バリアントなし。スキップします。")
        return

    logger.info(f"[A/Bテスト] {len(pending)}件を評価します...")
    learnings = _load_learnings()

    for variant in pending:
        title_a = variant.get("title_a", "")
        title_b = variant.get("title_b", "")
        if not title_a or not title_b:
            variant["evaluated"] = True
            continue

        prompt = f"""あなたはZenn記事のタイトル評価の専門家です。
同じテーマの2つのタイトルを比較し、どちらが読者にクリックされやすいか評価してください。

【テーマ】{variant.get("topic", "")}
【タイトルA】{title_a}（数字・具体的情報型）
【タイトルB】{title_b}（問いかけ・感情訴求型）

以下のセクション区切りで厳密に出力してください:

---EVAL_START---
WINNER: AまたはB（どちらか1文字）
WINNER_PATTERN: 勝者のタイトルパターン名（例: 数字型, 問いかけ型, 実績提示型）
REASON: 勝者を選んだ理由（50字以内）
LOSER_IMPROVEMENT: 負けたタイトルの改善ポイント（50字以内）
---EVAL_END---"""

        try:
            content, inp, out = _call_claude(client, prompt, model)
            _record_cost(inp, out)

            match = re.search(r"---EVAL_START---\s*(.*?)\s*---EVAL_END---", content, re.DOTALL)
            if not match:
                raise ValueError("評価レスポンスのパース失敗")

            block = match.group(1).strip()

            def extract(key: str) -> str:
                m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
                return m.group(1).strip() if m else ""

            winner = extract("WINNER").upper()
            winner_pattern = extract("WINNER_PATTERN")
            reason = extract("REASON")
            loser_improvement = extract("LOSER_IMPROVEMENT")

            result = {
                "date": variant.get("date", str(date.today())),
                "topic": variant.get("topic", ""),
                "title_a": title_a,
                "title_b": title_b,
                "winner": winner.lower(),
                "winner_title": title_a if winner == "A" else title_b,
                "winner_pattern": winner_pattern,
                "reason": reason,
                "loser_improvement": loser_improvement,
            }
            learnings["ab_results"].append(result)

            # パターン集計
            if winner_pattern:
                learnings["winning_patterns"][winner_pattern] = (
                    learnings["winning_patterns"].get(winner_pattern, 0) + 1
                )

            variant["evaluated"] = True
            variant["winner"] = winner.lower()
            logger.info(f"[A/Bテスト] 評価完了: 勝者={winner} ({winner_pattern}) | {reason}")

        except Exception as e:
            logger.error(f"[A/Bテスト] 評価エラー: {e}")
            variant["evaluated"] = True  # エラーでも評価済みにして無限ループ防止

    _save_learnings(learnings)
    AB_VARIANTS_PATH.write_text(json.dumps(variants, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[A/Bテスト] 勝者パターン集計: {learnings['winning_patterns']}")


# ── B. 構成最適化（Zenn版: quality_threshold を ±5 自動調整）─────────────────

def _optimize_config(articles: list, config: dict) -> dict:
    """
    品質スコアの分布を分析し quality_threshold を調整する。
    有料プラットフォーム移行時は記事価格の ±50 円調整に置き換える。
    """
    if len(articles) < 5:
        logger.info("[構成最適化] 記事数が不足しています（5件以上必要）。スキップします。")
        return config

    scores = [a.get("quality_score", 0) for a in articles if a.get("quality_score", 0) > 0]
    if not scores:
        return config

    avg = sum(scores) / len(scores)
    threshold = config.get("quality_threshold", 70)
    new_threshold = threshold

    # 平均スコアが高い → 閾値を上げて品質基準を引き上げる（最大85）
    # 平均スコアが低い → 閾値を下げて記事生成を通しやすくする（最低55）
    if avg >= threshold + 10 and threshold < 85:
        new_threshold = min(threshold + 5, 85)
        logger.info(f"[構成最適化] 品質スコア平均{avg:.1f}点 → quality_threshold: {threshold} → {new_threshold}")
    elif avg < threshold - 5 and threshold > 55:
        new_threshold = max(threshold - 5, 55)
        logger.info(f"[構成最適化] 品質スコア平均{avg:.1f}点 → quality_threshold: {threshold} → {new_threshold}")
    else:
        logger.info(f"[構成最適化] quality_threshold 変更なし（平均: {avg:.1f}点 / 閾値: {threshold}点）")

    config["quality_threshold"] = new_threshold

    # トピック別スコア分析
    topic_scores: dict = defaultdict(list)
    for a in articles:
        topic = a.get("topic", "")
        score = a.get("quality_score", 0)
        if topic and score > 0:
            first_word = topic.replace("・", " ").split()[0] if topic else ""
            if first_word:
                topic_scores[first_word].append(score)

    best_topics = sorted(
        ((t, sum(s) / len(s)) for t, s in topic_scores.items() if len(s) >= 2),
        key=lambda x: x[1],
        reverse=True,
    )[:3]
    if best_topics:
        logger.info("[構成最適化] 高スコアトピック: " + ", ".join(f"{t}({s:.0f}点)" for t, s in best_topics))

    return config


# ── C. テーマ発掘 ─────────────────────────────────────────────────────────────

def _discover_themes(articles: list, config: dict, client: anthropic.Anthropic, model: str) -> list:
    """
    投稿済み記事のパターンを分析し、読者が「もっと知りたい」テーマを抽出する。
    ZennにはコメントAPIがないため、トピッククラスターと品質スコアから推定する。
    """
    if len(articles) < 3:
        logger.info("[テーマ発掘] 記事数が不足しています（3件以上必要）。スキップします。")
        return []

    niche = config["niche"]
    recent = articles[-20:]  # 直近20件に絞る

    topic_list = "\n".join(
        f"- {a.get('topic', '')[:60]} (品質スコア: {a.get('quality_score', 0)}点)"
        for a in recent
        if a.get("topic")
    )

    # A/Bテストの学習データも参照
    learnings = _load_learnings()
    winning_patterns_str = json.dumps(learnings.get("winning_patterns", {}), ensure_ascii=False)

    prompt = f"""あなたは{niche}分野のコンテンツマーケターです。
以下の投稿済み記事リストを分析して、読者がさらに知りたいと思うテーマを発掘してください。

【投稿済み記事】
{topic_list}

【これまでのA/Bテスト勝者パターン】
{winning_patterns_str}

以下のセクション区切りで厳密に出力してください:

---THEMES_START---
NEW_KEYWORDS: キーワード1,キーワード2,キーワード3,キーワード4,キーワード5（カンマ区切り）
READER_NEEDS: 読者が最も知りたいこと（100字以内）
UNTAPPED_ANGLES: まだ書かれていない切り口（例: 具体的な税額シミュレーション, 失敗談からの学び）
---THEMES_END---"""

    try:
        content, inp, out = _call_claude(client, prompt, model)
        _record_cost(inp, out)

        match = re.search(r"---THEMES_START---\s*(.*?)\s*---THEMES_END---", content, re.DOTALL)
        if not match:
            raise ValueError("テーマ発掘レスポンスのパース失敗")

        block = match.group(1).strip()

        def extract(key: str) -> str:
            m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
            return m.group(1).strip() if m else ""

        new_kws_raw = extract("NEW_KEYWORDS")
        reader_needs = extract("READER_NEEDS")
        untapped = extract("UNTAPPED_ANGLES")

        new_keywords = [k.strip() for k in new_kws_raw.split(",") if k.strip()]

        # learnings.json にテーマ洞察を保存
        learnings["theme_insights"].append({
            "date": str(date.today()),
            "keywords": new_keywords,
            "reader_needs": reader_needs,
            "untapped_angles": untapped,
        })
        _save_learnings(learnings)
        logger.info(f"[テーマ発掘] 新キーワード: {new_keywords}")
        logger.info(f"[テーマ発掘] 読者ニーズ: {reader_needs}")
        return new_keywords

    except Exception as e:
        logger.error(f"[テーマ発掘] エラー: {e}")
        return []


# ── D. 月次サマリー ───────────────────────────────────────────────────────────

def generate_monthly_summary():
    """
    毎月1日に前月の実績を集計して logs/monthly_YYYY-MM.md に保存する。
    main.py の run_daily() から date.today().day == 1 のときに呼ばれる。
    """
    today = date.today()
    # 前月を対象とする
    if today.month == 1:
        target_year, target_month = today.year - 1, 12
    else:
        target_year, target_month = today.year, today.month - 1

    target_prefix = f"{target_year}-{target_month:02d}"
    output_path = MONTHLY_LOG_DIR / f"monthly_{target_prefix}.md"

    if output_path.exists():
        logger.info(f"[月次サマリー] {output_path} は既に存在します。スキップします。")
        return

    articles = _load_articles()
    month_articles = [a for a in articles if a.get("date", "").startswith(target_prefix)]

    # コスト集計
    month_cost = 0.0
    if COST_TRACKER_PATH.exists():
        tracker = json.loads(COST_TRACKER_PATH.read_text(encoding="utf-8"))
        if tracker.get("month") == target_prefix:
            month_cost = tracker.get("total_jpy", 0.0)

    # A/Bテスト集計
    learnings = _load_learnings()
    month_ab = [r for r in learnings.get("ab_results", []) if r.get("date", "").startswith(target_prefix)]
    winning_patterns = learnings.get("winning_patterns", {})
    top_pattern = max(winning_patterns, key=winning_patterns.get) if winning_patterns else "データなし"

    # 品質スコア統計
    scores = [a.get("quality_score", 0) for a in month_articles if a.get("quality_score", 0) > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    max_score = max(scores) if scores else 0
    min_score = min(scores) if scores else 0

    # トピック一覧
    topics_md = "\n".join(
        f"| {a.get('date', '')} | {a.get('title', '')[:40]} | {a.get('quality_score', 0)}点 |"
        for a in month_articles
    ) or "| - | データなし | - |"

    # テーマ洞察（当月分）
    month_themes = [t for t in learnings.get("theme_insights", []) if t.get("date", "").startswith(target_prefix)]
    theme_md = ""
    if month_themes:
        latest = month_themes[-1]
        theme_md = f"""
## C. テーマ発掘

- **読者ニーズ**: {latest.get("reader_needs", "")}
- **未開拓の切り口**: {latest.get("untapped_angles", "")}
- **来月のキーワード**: {", ".join(latest.get("keywords", []))}
"""

    content = f"""# 月次サマリー {target_year}年{target_month}月

生成日: {today}

---

## A. 投稿実績

- **投稿記事数**: {len(month_articles)} 件
- **平均品質スコア**: {avg_score} 点（最高: {max_score} / 最低: {min_score}）
- **今月のAPIコスト**: {month_cost:.0f} 円

### 記事一覧

| 投稿日 | タイトル | 品質スコア |
|--------|----------|-----------|
{topics_md}

---

## B. A/Bテスト結果

- **評価件数**: {len(month_ab)} 件
- **累計勝者パターン1位**: {top_pattern}（{winning_patterns.get(top_pattern, 0)}勝）

### 全パターン集計

| パターン | 勝利数 |
|----------|--------|
{"".join(f"| {p} | {c}勝 |" + chr(10) for p, c in sorted(winning_patterns.items(), key=lambda x: x[1], reverse=True)) or "| データなし | - |"}
{theme_md}
---

## D. 来月へのアクション

1. 勝者パターン「{top_pattern}」を記事タイトルに積極採用
2. 発掘テーマをもとにscoutが自動検索キーワードを更新済み
3. APIコスト残高を確認 → 上限まで残り {max(0, _load_config().get("monthly_cost_limit", 2000) - month_cost):.0f} 円

---
*このレポートは自動生成されました*
"""

    MONTHLY_LOG_DIR.mkdir(exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logger.info(f"[月次サマリー] 保存完了: {output_path}")
    return str(output_path)


# ── E. オーナー思考学習 ───────────────────────────────────────────────────────

OWNER_DIR = Path("owner")
OWNER_THOUGHTS_PATH = OWNER_DIR / "thoughts.md"
OWNER_LEARNINGS_PATH = OWNER_DIR / "learnings.md"
OWNER_CONTEXT_PATH = OWNER_DIR / "context_prompt.md"


def _learn_owner_thoughts(client: anthropic.Anthropic, model: str):
    """
    owner/thoughts.md を読み、オーナーの思考傾向をJSON抽出して
    owner/learnings.md と owner/context_prompt.md を自動更新する。
    """
    if not OWNER_THOUGHTS_PATH.exists():
        logger.info("[オーナー学習] thoughts.md が見つかりません。スキップします。")
        return

    thoughts = OWNER_THOUGHTS_PATH.read_text(encoding="utf-8").strip()
    if not thoughts:
        return

    prompt = f"""以下はシステムオーナーの思考メモです。

{thoughts}

このメモから読み取れるオーナーの特徴を、以下のJSON形式で抽出してください。

{{
  "priorities": ["最も優先していること（3〜5項目）"],
  "decision_criteria": ["判断基準（3〜5項目）"],
  "avoid": ["避けていること・嫌いなこと（2〜4項目）"],
  "values": ["大事にしていること（3〜5項目）"],
  "context_summary": "AIへの注入プロンプト用まとめ（150字以内）"
}}

JSONのみ出力してください。説明や前置きは不要です。"""

    try:
        content, inp, out = _call_claude(client, prompt, model, max_tokens=800)
        _record_cost(inp, out)

        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            raise ValueError("JSON抽出失敗")
        profile = json.loads(json_match.group())
        today_str = str(date.today())

        # owner/learnings.md に週次エントリを追記
        OWNER_DIR.mkdir(exist_ok=True)
        existing = OWNER_LEARNINGS_PATH.read_text(encoding="utf-8") if OWNER_LEARNINGS_PATH.exists() else ""
        lines = []
        lines.append(f"\n## {today_str} 週次学習\n")
        lines.append("**優先順位**")
        lines.extend(f"- {p}" for p in profile.get("priorities", []))
        lines.append("\n**判断基準**")
        lines.extend(f"- {c}" for c in profile.get("decision_criteria", []))
        lines.append("\n**避けること**")
        lines.extend(f"- {a}" for a in profile.get("avoid", []))
        lines.append("\n**大事にしていること**")
        lines.extend(f"- {v}" for v in profile.get("values", []))
        lines.append("")
        OWNER_LEARNINGS_PATH.write_text(existing.rstrip() + "\n" + "\n".join(lines), encoding="utf-8")

        # owner/context_prompt.md を上書き更新
        priorities_md = "\n".join(f"- {p}" for p in profile.get("priorities", []))
        criteria_md = "\n".join(f"- {c}" for c in profile.get("decision_criteria", []))
        values_md = "\n".join(f"- {v}" for v in profile.get("values", []))
        summary = profile.get("context_summary", "")

        OWNER_CONTEXT_PATH.write_text(
            f"# オーナーコンテキスト（AIへの自動注入プロンプト）\n\n"
            f"<!-- analyst.py が週次で自動更新。最終更新: {today_str} -->\n\n"
            f"このシステムのオーナーの特徴:\n"
            f"{priorities_md}\n"
            f"{criteria_md}\n"
            f"{values_md}\n\n"
            f"{summary}\n\n"
            f"これらを常に考慮して判断・実行してください。\n",
            encoding="utf-8",
        )
        logger.info("[オーナー学習] learnings.md と context_prompt.md を更新しました")

    except Exception as e:
        logger.error("[オーナー学習] エラー: %s", e)


# ── F. 行動確信プロトコル（週次証明） ──────────────────────────────────────────

def _prove_weekly_observations(
    client: anthropic.Anthropic,
    model: str,
    articles: list,
    config: dict,
    summary: dict,
) -> None:
    """週次観測から命題を3つ立てて全で証明し、proven_propositions.jsonに蓄積する"""
    try:
        from empire.proposition_lib import (
            load_propositions, find_similar,
            prove_proposition, add_proposition,
            format_weekly_discord, append_proof_log,
        )
        from empire.utils import get_master_os, notify
    except ImportError as e:
        logger.warning("[確信プロトコル] インポート失敗: %s", e)
        return

    master_os = get_master_os()
    props_data = load_propositions()

    niche = config.get("niche", "副業・節税")
    avg_score = summary.get("avg_quality_score", 0)
    top_kw = (config.get("auto_strategy", {}).get("top_keywords") or [])[:3]
    kw_str = "・".join(top_kw) if top_kw else niche

    candidates = [
        (
            f"「{kw_str}」テーマの記事は今の読者に強く刺さる",
            f"今週の上位キーワード: {kw_str} / 平均品質スコア: {avg_score}点 / 直近7日記事数: {summary.get('recent_7days_count', 0)}本",
            "コンテンツ",
        ),
        (
            f"{niche}ジャンルの記事は朝6時投稿が最も読まれる",
            f"Zenn読者属性: 副業・エンジニア / 朝の可処分時間が最大 / 投稿履歴: {summary.get('total_articles', 0)}本",
            "タイミング",
        ),
        (
            f"品質スコア{int(avg_score)}点以上の記事は低品質記事の2倍以上スキを獲得する",
            f"今週の平均品質スコア: {avg_score}点 / A/Bテスト勝者パターン: {summary.get('top_ab_pattern', '未確定')}",
            "品質",
        ),
    ]

    this_week_ids: list[str] = []
    for proposition, observation, domain in candidates:
        try:
            similar = find_similar(proposition, props_data)
            proof = prove_proposition(client, model, proposition, observation, master_os, domain, similar)
            record = add_proposition(props_data, proof, domain, "週次分析から自動生成")
            this_week_ids.append(record["id"])
            logger.info(
                "[確信プロトコル] 証明完了 — %s（確信度: %d%%）",
                proposition[:40], proof.get("confidence", 0),
            )
            # 出版記録: proof_log.md に追記
            try:
                append_proof_log(
                    proposition=proof.get("proposition", proposition),
                    universal_truth=proof.get("universal_truth", ""),
                    result="",
                    context=f"週次分析 — {domain}",
                )
            except Exception:
                pass
            # ライブラリを最新状態に更新
            props_data = load_propositions()
        except Exception as e:
            logger.warning("[確信プロトコル] 命題証明エラー: %s", e)

    # Discord 週次証明サマリーを通知
    if this_week_ids:
        try:
            section = format_weekly_discord(props_data, this_week_ids)
            notify("🔬 今週の証明サマリー（確信プロトコル）", section)
        except Exception as e:
            logger.warning("[確信プロトコル] Discord通知失敗: %s", e)


# ── 週次メイン処理 ────────────────────────────────────────────────────────────

def run():
    config = _load_config()
    monthly_limit = config.get("monthly_cost_limit", 2000)

    # 安全装置: コスト上限チェック
    if _get_month_cost() >= monthly_limit:
        logger.error("月間コスト上限に達しているため週次分析をスキップします")
        return

    articles = _load_articles()
    if not articles:
        logger.info("分析対象の記事がありません。スキップします。")
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = config.get("model", "claude-sonnet-4-6")

    # ── A. A/Bテスト評価 ──
    logger.info("=== A. A/Bテスト評価 ===")
    _evaluate_ab_tests(client, model)

    # ── B. 構成最適化 ──
    logger.info("=== B. 構成最適化 ===")
    config = _optimize_config(articles, config)

    # ── C. テーマ発掘 ──
    logger.info("=== C. テーマ発掘 ===")
    new_themes = _discover_themes(articles, config, client, model)

    # ── 週次戦略更新（既存ロジック + 発掘テーマを統合）──
    logger.info("=== 週次戦略更新 ===")

    cutoff = str(date.today() - timedelta(days=7))
    recent_articles = [a for a in articles if a.get("date", "") >= cutoff]

    word_counter = Counter()
    for a in articles:
        words = a.get("topic", "").replace("・", " ").replace("　", " ").split()
        word_counter.update(w for w in words if len(w) > 1)
    freq_keywords = [kw for kw, _ in word_counter.most_common(8)]

    # 発掘テーマを優先的にキーワードに追加（重複除去）
    merged_keywords = list(dict.fromkeys(new_themes + freq_keywords))[:10]

    scores = [a.get("quality_score", 0) for a in articles if a.get("quality_score", 0) > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    # A/Bテストの勝者パターンを戦略に反映
    learnings = _load_learnings()
    top_pattern = ""
    if learnings.get("winning_patterns"):
        top_pattern = max(learnings["winning_patterns"], key=learnings["winning_patterns"].get)

    summary = {
        "niche": config["niche"],
        "total_articles": len(articles),
        "recent_7days_count": len(recent_articles),
        "avg_quality_score": avg_score,
        "recent_topics": [a.get("topic", "")[:50] for a in articles[-8:]],
        "current_month_cost_jpy": round(_get_month_cost(), 1),
        "monthly_limit_jpy": monthly_limit,
        "top_ab_pattern": top_pattern,
        "new_discovered_themes": new_themes[:3],
    }

    prompt = f"""あなたはZennメディアのコンテンツ戦略コンサルタントです。
以下のデータを分析して、来週の記事戦略を提案してください。

【データ】
{json.dumps(summary, ensure_ascii=False, indent=2)}

以下のセクション区切りで厳密に出力してください:

---STRATEGY_START---
TOP_KEYWORDS: キーワード1,キーワード2,キーワード3（最大5個、カンマ区切り）
BEST_STYLE: 推奨文体（A/Bテスト結果の勝者パターンも考慮すること）
WEEKLY_REPORT: 今週の振り返りと来週への提言（200字以内）
---STRATEGY_END---"""

    try:
        content, inp, out = _call_claude(client, prompt, model)
        _record_cost(inp, out)

        match = re.search(r"---STRATEGY_START---\s*(.*?)\s*---STRATEGY_END---", content, re.DOTALL)
        if not match:
            raise ValueError("戦略レスポンスのパース失敗")

        block = match.group(1).strip()

        def extract(key: str) -> str:
            m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
            return m.group(1).strip() if m else ""

        new_kws = [k.strip() for k in extract("TOP_KEYWORDS").split(",") if k.strip()]
        best_style = extract("BEST_STYLE")
        weekly_report = extract("WEEKLY_REPORT")

        config["auto_strategy"]["top_keywords"] = (new_kws or merged_keywords)[:10]
        config["auto_strategy"]["best_style"] = best_style
        config["auto_strategy"]["weekly_report"] = weekly_report
        config["auto_strategy"]["last_updated"] = str(date.today())
        _save_config(config)
        logger.info(f"[週次戦略] 更新完了: {weekly_report[:60]}...")

    except Exception as e:
        logger.error(f"[週次戦略] Claude API失敗: {e}。フォールバック更新します。")
        config["auto_strategy"]["top_keywords"] = merged_keywords[:10]
        config["auto_strategy"]["last_updated"] = str(date.today())
        _save_config(config)

    # ── E. オーナー思考学習 ──
    logger.info("=== E. オーナー思考学習 ===")
    _learn_owner_thoughts(client, model)

    # ── F. 行動確信プロトコル — 週次観測から命題を証明 ──
    logger.info("=== F. 行動確信プロトコル（週次証明）===")
    _prove_weekly_observations(client, model, articles, config, summary)
