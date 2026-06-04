"""
ジャンル抽象化・具体化エンジン

テーマツリーを自動で深掘り/巻き戻しし、ネタ切れを防ぐ。

ルール:
  - 同じレベルのテーマを DRILL_DOWN_THRESHOLD 回書いたら1段具体化
  - 同じ枝を EXHAUST_THRESHOLD 回書いたら1段抽象に戻り別の枝を探す
  - affiliates.yaml と照合して広告と相性の良いテーマを優先

履歴: data/genre_history.json に自動蓄積
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 定数 ───────────────────────────────────────────────────────────────────────
DRILL_DOWN_THRESHOLD = 3   # 同レベルで何回書いたら具体化するか
EXHAUST_THRESHOLD = 5      # 同じ枝で何回書いたらその枝を諦めるか
MAX_LEVEL = 5              # 具体化の最大深さ

# ── ルールベース具体化マップ ──────────────────────────────────────────────────
# (parent_theme_keyword → [(direction_label, [child_themes])])
CONCRETIZE_MAP: dict[str, list[tuple[str, list[str]]]] = {
    "恋愛": [
        ("場面", [
            "恋愛・告白できない理由",
            "恋愛・LINEの返信が怖い",
            "恋愛・既読スルーされたときの心理",
            "恋愛・初デートで緊張する構造",
            "恋愛・別れの予感がするとき",
            "恋愛・復縁を引きずる理由",
        ]),
        ("感情", [
            "恋愛・好きになりすぎてしまう人の構造",
            "恋愛・嫉妬の正体",
            "恋愛・依存してしまう理由",
            "恋愛・さみしさと愛情の違い",
        ]),
    ],
    "人間関係": [
        ("場面", [
            "人間関係・職場で消耗する構造",
            "人間関係・断れない人の設計",
            "人間関係・気を遣いすぎる理由",
            "人間関係・友人との温度差",
            "人間関係・家族との距離感",
            "人間関係・SNS比較の罠",
        ]),
        ("感情", [
            "人間関係・孤独感の正体",
            "人間関係・承認欲求と自己肯定感",
            "人間関係・怒りが消えない理由",
        ]),
    ],
    "自己成長": [
        ("場面", [
            "自己成長・朝の習慣が続かない構造",
            "自己成長・比べてしまう瞬間の設計",
            "自己成長・失敗した翌日の心理",
            "自己成長・やる気が出ない日の正体",
            "自己成長・完璧主義の裏設定",
        ]),
        ("感情", [
            "自己成長・自己嫌悪の構造",
            "自己成長・焦りの正体",
            "自己成長・変われない理由の言語化",
        ]),
    ],
    "自己肯定感": [
        ("場面", [
            "自己肯定感・褒められても信じられないとき",
            "自己肯定感・鏡を見るのが嫌な日の構造",
            "自己肯定感・成功しても虚しい理由",
        ]),
        ("感情", [
            "自己肯定感・「どうせ自分は」の正体",
            "自己肯定感・他人の目が気になる構造",
        ]),
    ],
    "習慣": [
        ("場面", [
            "習慣・三日坊主の構造的正体",
            "習慣・スマホをやめられない理由",
            "習慣・夜型から抜け出せない設計",
        ]),
        ("感情", [
            "習慣・続けようとすると続かない逆説",
            "習慣・意志力を消耗させる設計ミス",
        ]),
    ],
    "感情": [
        ("場面", [
            "感情・怒りが止まらない夜の構造",
            "感情・不安が消えない理由",
            "感情・涙の正体",
        ]),
        ("感情_detail", [
            "感情・感情を言語化できない苦しさ",
            "感情・無感覚になっていく理由",
        ]),
    ],
    "哲学": [
        ("概念", [
            "哲学・「普通」という言葉の罠",
            "哲学・努力が報われないとき何が起きているか",
            "哲学・言語化できないことの正体",
        ]),
        ("問い", [
            "哲学・幸せの定義が間違っている理由",
            "哲学・「自分らしさ」という概念の危うさ",
        ]),
    ],
    "婚活": [
        ("場面", [
            "婚活・マッチングアプリで疲弊する構造",
            "婚活・いい人なのに踏み出せない理由",
            "婚活・理想の相手を逃し続ける設計",
        ]),
        ("感情", [
            "婚活・焦りと諦めの間にいる人",
            "婚活・好きじゃないのに断れない心理",
        ]),
    ],
}

# ── データ構造 ────────────────────────────────────────────────────────────────

def _empty_history() -> dict:
    return {
        "current_branch": {
            "root_genre": "",
            "path": [],
            "current_theme": "",
            "level": 1,
            "consecutive_at_level": 0,
        },
        "articles": [],
        "exhausted_branches": [],
    }


# ── ファイルI/O ────────────────────────────────────────────────────────────────

def load_history(data_dir: Path) -> dict:
    path = data_dir / "data" / "genre_history.json"
    if not path.exists():
        return _empty_history()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_history()


def save_history(data_dir: Path, history: dict) -> None:
    path = data_dir / "data" / "genre_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _count_recent(articles: list[dict], theme: str, n: int = 10) -> int:
    """直近 n 件の記事のうち同テーマのものを数える"""
    recent = articles[-n:] if len(articles) >= n else articles
    return sum(1 for a in recent if a.get("theme", "") == theme)


def _count_branch(articles: list[dict], root_genre: str, n: int = 20) -> int:
    """直近 n 件の記事のうち同じルートジャンルのものを数える"""
    recent = articles[-n:] if len(articles) >= n else articles
    return sum(1 for a in recent if a.get("root_genre", "") == root_genre)


def _priority_score(theme: str, affiliates: list[dict], hot_genres: set[str]) -> int:
    """
    テーマの優先スコアを返す（高いほど優先）
      +2: affiliates.yaml のジャンルと一致
      +1: note_patterns.json の resonance_structures ジャンルと一致
    """
    score = 0
    for af in affiliates:
        for g in af.get("genres", []):
            if g in theme:
                score += 2
                break  # 同一affiliate で多重カウントしない
    for g in hot_genres:
        if g and g in theme:
            score += 1
            break  # 多重カウントしない
    return score


def _load_affiliates(affiliates_path: Path) -> list[dict]:
    if not affiliates_path.exists():
        return []
    try:
        import yaml
        data = yaml.safe_load(affiliates_path.read_text(encoding="utf-8"))
        return (data or {}).get("affiliates", [])
    except Exception:
        return []


def _load_hot_genres(patterns_path: Path) -> set[str]:
    """
    note_patterns.json の resonance_structures から
    「今読まれているジャンル」を抽出して返す。
    旧 main.py の hot_genres ロジックを移植。
    """
    if not patterns_path.exists():
        return set()
    try:
        pt = json.loads(patterns_path.read_text(encoding="utf-8"))
        return {
            rs.get("genre", "")
            for rs in pt.get("latest", {}).get("resonance_structures", [])
            if rs.get("genre", "")
        }
    except Exception:
        return set()


# ── 具体化ロジック ─────────────────────────────────────────────────────────────

def _find_concretize_candidates(current_theme: str, exhausted: list[str]) -> list[str]:
    """CONCRETIZE_MAP からキーワードに合う候補を返す"""
    candidates: list[str] = []
    for key, direction_list in CONCRETIZE_MAP.items():
        if key in current_theme:
            for _, children in direction_list:
                for child in children:
                    if child not in exhausted:
                        candidates.append(child)
    return candidates


def _concretize_with_claude(
    current_theme: str,
    level: int,
    client: Any,
    model: str,
) -> str | None:
    """Claude API で具体化テーマを生成する（利用可能な場合のみ）"""
    try:
        prompt = f"""
以下のテーマをnote.com記事向けに「1段階だけ」具体化してください。

現在のテーマ: {current_theme}
現在の抽象度レベル: {level}/5（1=抽象、5=具体）

具体化の方向は2つから選んでください:
- 場面を絞る（例: 恋愛 → 告白できない理由）
- 感情を絞る（例: 恋愛 → 嫉妬の正体）

条件:
- 読者が「自分のことだ」と感じられる具体的なテーマ
- 20文字以内
- note.comで読まれる自己成長・恋愛・人間関係ジャンルに適したもの

具体化したテーマを1つだけ返してください。説明不要。テーマ名のみ。
"""
        resp = client.messages.create(
            model=model,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp.content[0].text.strip().strip("「」")
        if result and len(result) <= 40:
            return result
    except Exception as exc:
        logger.debug("Claude具体化失敗: %s", exc)
    return None


# ── メインロジック ─────────────────────────────────────────────────────────────

def get_next_theme(
    data_dir: Path,
    genre_rotation: list[str],
    current_idx: int,
    affiliates_path: Path | None = None,
    patterns_path: Path | None = None,
    client: Any = None,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """
    次に書くべきテーマを決定して返す。

    Args:
        affiliates_path: owner/affiliates.yaml のパス（ジャンルスコア +2）
        patterns_path:   owner/note_patterns.json のパス（hot_genres スコア +1）

    Returns:
        {
            "theme": "具体テーマ文字列",
            "level": 1-5,
            "root_genre": "ルートジャンル",
            "next_genre_index": int,
            "reasoning": "選定理由",
        }
    """
    history = load_history(data_dir)
    branch = history.get("current_branch", {})
    articles = history.get("articles", [])
    exhausted = history.get("exhausted_branches", [])
    affiliates = _load_affiliates(affiliates_path) if affiliates_path else []
    hot_genres = _load_hot_genres(patterns_path) if patterns_path else set()
    if hot_genres:
        logger.debug("hot_genres from note_patterns.json: %s", hot_genres)

    current_theme = branch.get("current_theme", "")
    current_level = branch.get("level", 1)
    root_genre = branch.get("root_genre", "")
    consecutive = branch.get("consecutive_at_level", 0)
    path = branch.get("path", [])

    # ── ケース1: 同じ枝を EXHAUST_THRESHOLD 回書いた → 1段抽象に戻る ──────────
    branch_count = _count_branch(articles, root_genre, n=20)
    if current_theme and branch_count >= EXHAUST_THRESHOLD and current_level > 1:
        exhausted.append(current_theme)
        # 1段上に戻る
        parent_theme = path[-2] if len(path) >= 2 else root_genre
        new_level = current_level - 1
        logger.info(
            "枝が枯渇（%d回）→ 1段抽象化: %s → %s",
            branch_count, current_theme, parent_theme,
        )
        _update_branch(history, parent_theme, new_level, root_genre, path[:-1])
        return _build_result(
            parent_theme, new_level, root_genre,
            current_idx, f"枝枯渇のため1段抽象化（{current_theme}を{branch_count}回書いた）",
            history, data_dir,
        )

    # ── ケース2: 同じレベルを DRILL_DOWN_THRESHOLD 回書いた → 1段具体化 ────────
    if current_theme and consecutive >= DRILL_DOWN_THRESHOLD and current_level < MAX_LEVEL:
        candidates = _find_concretize_candidates(current_theme, exhausted)

        # Claude が使える場合はより賢く具体化
        new_theme = None
        if client and not candidates:
            new_theme = _concretize_with_claude(current_theme, current_level, client, model)

        if not new_theme and candidates:
            # アフィリエイト＋市場パターンスコアで優先順
            candidates.sort(key=lambda c: -_priority_score(c, affiliates, hot_genres))
            new_theme = candidates[0]

        if new_theme and new_theme not in exhausted:
            new_level = current_level + 1
            new_path = path + [new_theme]
            logger.info(
                "同レベル%d回 → 1段具体化: %s → %s (level %d→%d)",
                consecutive, current_theme, new_theme, current_level, new_level,
            )
            _update_branch(history, new_theme, new_level, root_genre, new_path)
            return _build_result(
                new_theme, new_level, root_genre,
                current_idx, f"{consecutive}回同レベル→具体化（{current_theme}→{new_theme}）",
                history, data_dir,
            )

    # ── ケース3: 通常継続 または 初回 ─────────────────────────────────────────
    if not current_theme or not root_genre:
        # 初回: genre_rotation からアフィリエイト＋市場パターンスコアが高いものを選ぶ
        scored_genres = sorted(
            genre_rotation,
            key=lambda g: -_priority_score(g, affiliates, hot_genres),
        )
        chosen = scored_genres[0] if scored_genres else genre_rotation[current_idx % len(genre_rotation)]
        new_idx = (genre_rotation.index(chosen) + 1) % len(genre_rotation) if chosen in genre_rotation else (current_idx + 1) % len(genre_rotation)
        root_key = _extract_root(chosen)
        reason = _select_reason(chosen, affiliates, hot_genres)
        logger.info("初回: ジャンル選定 → %s（%s）", chosen, reason)
        _update_branch(history, chosen, 1, root_key, [chosen])
        return _build_result(chosen, 1, root_key, new_idx, f"初回選定（{reason}）", history, data_dir)

    # 継続: consecutive を+1して同テーマを返す
    history["current_branch"]["consecutive_at_level"] = consecutive + 1
    logger.info(
        "テーマ継続: %s (level=%d, consecutive=%d→%d)",
        current_theme, current_level, consecutive, consecutive + 1,
    )
    return _build_result(
        current_theme, current_level, root_genre,
        current_idx, f"継続（同テーマ {consecutive+1}回目）",
        history, data_dir,
    )


def _select_reason(theme: str, affiliates: list[dict], hot_genres: set[str]) -> str:
    """選定理由を人間可読な文字列で返す（旧 _genre_select_reason の移植）"""
    reasons = []
    if any(g in theme for af in affiliates for g in af.get("genres", [])):
        reasons.append("アフィリエイト案件あり")
    if any(g and g in theme for g in hot_genres):
        reasons.append("市場パターン一致")
    return "、".join(reasons) if reasons else "通常ローテーション"


def _extract_root(genre: str) -> str:
    """ジャンル文字列からルートキーワードを抽出する"""
    for key in CONCRETIZE_MAP:
        if key in genre:
            return key
    # 「・」区切りの最初の語を使う
    return genre.split("・")[0].split("—")[0].strip()


def _update_branch(
    history: dict,
    theme: str,
    level: int,
    root_genre: str,
    path: list[str],
) -> None:
    history["current_branch"] = {
        "root_genre": root_genre,
        "path": path,
        "current_theme": theme,
        "level": level,
        "consecutive_at_level": 1,
    }


def _build_result(
    theme: str,
    level: int,
    root_genre: str,
    next_idx: int,
    reasoning: str,
    history: dict,
    data_dir: Path,
) -> dict:
    save_history(data_dir, history)
    return {
        "theme": theme,
        "level": level,
        "root_genre": root_genre,
        "next_genre_index": next_idx,
        "reasoning": reasoning,
    }


# ── 記事記録 ──────────────────────────────────────────────────────────────────

def record_article(
    data_dir: Path,
    title: str,
    theme: str | None = None,
) -> None:
    """
    記事生成後に呼び出して履歴に記録する。
    theme が None の場合は current_branch.current_theme を使用。
    """
    history = load_history(data_dir)
    branch = history.get("current_branch", {})
    theme = theme or branch.get("current_theme", "")
    root_genre = branch.get("root_genre", "")
    level = branch.get("level", 1)

    history.setdefault("articles", []).append({
        "date": str(date.today()),
        "theme": theme,
        "level": level,
        "root_genre": root_genre,
        "title": title,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })

    save_history(data_dir, history)
    logger.info("記事記録: theme=%s level=%d title=%s", theme, level, title[:30])
