"""
Creator: Claude でnote.com向け有料記事を生成し下書きとして保存する

API利用可能時  : Claude API で高品質記事を生成
API利用不可時  : note_patterns.json + voice_os.md でテンプレート記事を生成
どちらの場合も : ready/YYYY-MM-DD.md に保存し、パスを返す
"""
import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
PATTERNS_PATH = PROJECT_ROOT / "owner" / "note_patterns.json"
AFFILIATES_PATH = PROJECT_ROOT / "owner" / "affiliates.yaml"
VOICE_OS_PATH = PROJECT_ROOT / "thoughts" / "voice_os.md"


def _load_patterns() -> dict:
    if not PATTERNS_PATH.exists():
        return {}
    try:
        return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_affiliates() -> list[dict]:
    """owner/affiliates.yaml からアフィリエイトリストを読み込む"""
    if not AFFILIATES_PATH.exists() or not _YAML_AVAILABLE:
        return []
    try:
        data = _yaml.safe_load(AFFILIATES_PATH.read_text(encoding="utf-8"))
        return data.get("affiliates", []) if data else []
    except Exception as exc:
        logger.debug("affiliates.yaml 読み込み失敗: %s", exc)
        return []


def _select_affiliates(genre: str, topic_title: str, max_count: int = 2) -> list[dict]:
    """ジャンル・トピックタイトルに合致するアフィリエイトを最大 max_count 件返す"""
    affiliates = _load_affiliates()
    if not affiliates:
        return []

    # マッチング対象テキスト（ジャンル＋タイトルを結合）
    target = (genre + " " + topic_title).lower()

    scored: list[tuple[int, dict]] = []
    for af in affiliates:
        score = 0
        # "genres" キーを優先、なければ旧来の "category" にフォールバック
        for cat in af.get("genres", af.get("category", [])):
            if cat in target or any(c in cat for c in target.split()):
                score += 1
        if score > 0:
            scored.append((score, af))

    # スコア降順でソートして上位 max_count 件
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [af for _, af in scored[:max_count]]

    # URLがプレースホルダーのもの（"..."含む）はスキップ
    selected = [af for af in selected if "..." not in af.get("url", "...")]

    return selected


def _append_affiliate_section(body: str, affiliates: list[dict]) -> str:
    """記事本文末尾にアフィリエイトセクションを挿入する"""
    if not affiliates:
        return body

    lines = ["\n---", "この記事に関連するサービス"]
    for af in affiliates:
        name = af.get("name", "")
        url = af.get("url", "")
        desc = af.get("description", "")
        if desc:
            lines.append(f"・[{name}]({url})  \n　{desc}")
        else:
            lines.append(f"・[{name}]({url})")
    lines.append("---")

    logger.info("アフィリエイト挿入: %s", [af.get("id") for af in affiliates])
    return body + "\n".join(lines)


def _load_voice_os() -> str:
    """thoughts/voice_os.md を読み込む"""
    if not VOICE_OS_PATH.exists():
        return ""
    try:
        return VOICE_OS_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


def _slugify(text: str) -> str:
    """タイトルからファイル名用スラグを生成する"""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:40] or "article"


def _build_prompt(niche: str, topic: dict, config: dict, abstraction_meta: dict | None = None) -> str:
    """著者の思考OS + 文体OSを注入したプロンプトを構築する"""
    title_hint = topic.get("title", "")
    keywords = ", ".join(topic.get("keywords", []))
    today_genre = config.get("today_genre", niche)
    voice_os = config.get("voice_os", "")
    human_writing_os = config.get("human_writing_os", "")
    thought_seeds = config.get("thought_seeds", "")
    magazine_url = f"https://note.com/{config.get('note_user_id','militech_2077')}/m/{config.get('magazine_id','')}"
    author = config.get("author_pen_name", "ミリテク")

    parts = [
        f"あなたは「{author}」のゴーストライターAIです。",
        f"以下の2つのOSを完全に体現した記事を書いてください。",
        "",
    ]

    if voice_os:
        parts += [
            "## [OS-1] 著者の思考OS（全1論・一即全・100%再現論）",
            "※ 記事の「哲学的背骨」。全ての文章がここから派生する。",
            voice_os.strip(),
            "",
        ]

    if human_writing_os:
        parts += [
            "## [OS-2] 文体OS（生活の解像度が高い個人ブロガー）",
            "※ 記事の「皮膚感覚」。読者がAI臭を感じない文体を作る。OS-1の思想をOS-2の文体で表現する。",
            human_writing_os.strip(),
            "",
        ]

    if thought_seeds:
        parts += [
            "## 著者の思考シード（本日のインプット）",
            thought_seeds.strip(),
            "",
        ]

    # ── note_patterns.json からパターンを注入 ──────────────────────────────
    patterns = _load_patterns()
    if patterns.get("latest"):
        latest = patterns["latest"]
        tp = latest.get("title_patterns", {})
        bp = latest.get("body_patterns", {})
        formula = tp.get("title_formula", "")
        power_words = tp.get("power_words", [])
        avoid_words = tp.get("avoid_words", [])
        opening_types = bp.get("opening_types", [])
        conclusion_style = bp.get("conclusion_style", "")
        market_insight = latest.get("market_insight", "")
        # ジャンルに対応するresonance構造を探す
        resonance = {}
        for rs in latest.get("resonance_structures", []):
            if today_genre and rs.get("genre", "") in today_genre:
                resonance = rs
                break
        if not resonance and latest.get("resonance_structures"):
            resonance = latest["resonance_structures"][0]

        pattern_lines = ["## 市場パターン分析（必ず記事に反映）"]
        if formula:
            pattern_lines.append(f"タイトル公式: {formula}")
        if power_words:
            pattern_lines.append(f"パワーワード（積極使用）: {', '.join(power_words[:5])}")
        if avoid_words:
            pattern_lines.append(f"避けるワード: {', '.join(avoid_words[:3])}")
        if opening_types:
            pattern_lines.append(f"冒頭パターン: {', '.join(opening_types[:2])}")
        if conclusion_style:
            pattern_lines.append(f"結論の型: {conclusion_style}")
        if market_insight:
            pattern_lines.append(f"今の市場: {market_insight}")
        if resonance:
            pattern_lines += [
                f"刺さる構造: {resonance.get('abstract_structure', '')}",
                f"読者心理: {resonance.get('reader_psychology', '')}",
                f"再現パターン: {resonance.get('replicable_pattern', '')}",
            ]
        parts += pattern_lines + [""]

    if abstraction_meta:
        surface = abstraction_meta.get("surface_trend", "")
        abstract = abstraction_meta.get("abstract_structure", "")
        psychology = abstraction_meta.get("reader_psychology", "")
        pattern = abstraction_meta.get("replicable_pattern", "")
        if abstract or pattern:
            parts += [
                "## STEP3: 市場分析 → 抽象構造の翻訳（最重要）",
                "今日の記事はこの構造を本日のジャンルに翻訳したものにしてください。",
                f"・表面トレンド: {surface}",
                f"・なぜ読まれるかの本質: {abstract}",
                f"・読者の深層心理: {psychology}",
                f"・翻訳パターン（これを本日ジャンルで再現する）: {pattern}",
                "",
            ]

    parts += [
        "## 本日のジャンル",
        today_genre,
        "",
        "## トピック",
        title_hint,
        f"キーワード: {keywords}",
        "",
        "## 出力形式（JSONのみ・コードブロック・前置き一切不要）",
        "{",
        '  "title_a": "【20文字以内】具体的な感情/場面＋断言または問い。パターン分析のパワーワードを活用",',
        '  "title_b": "【20文字以内】読者が「自分のことだ」と感じる別パターン",',
        '  "hashtags": ["タグ1", "タグ2", "タグ3", "タグ4", "タグ5"],',
        '  "body": "記事本文（note Markdown形式、2000〜2500文字）"',
        "}",
        "",
        "## body執筆の指示",
        "- 一人称は「私」。「ぼく」「僕」は絶対に使わない",
        "- 「ぼくたちが」「僕たちが」は「人は」「読者は」または文を組み替えて自然な表現に",
        "- 冒頭2〜3文で読者を掴む（「この記事では」禁止・経験か感情から始める）",
        "- 全1論の構造を、ジャンル固有の言葉に翻訳する（抽象→具体の順）",
        "- 数字か固有名詞で具体性を担保する",
        "- FXの背景は1〜2文、さらっと添える程度",
        "- 最後の一文は「まとめ」ではなく読者の心に余韻を残す断言で終える",
        "",
        f"本文末尾（まとめの後）に必ず追加:\n---\nこのマガジンでは、全ジャンルに通底する「再現の法則」を毎日翻訳しています。\n→ {magazine_url}\n---",
    ]

    return "\n".join(parts)


def _generate_article(
    client: anthropic.Anthropic,
    model: str,
    niche: str,
    topic: dict,
    config: dict,
    abstraction_meta: dict | None = None,
) -> dict:
    """1記事分のコンテンツを Claude で生成する"""
    prompt = _build_prompt(niche, topic, config, abstraction_meta)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    try:
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        logger.info(
            "Claude usage — input: %d, output: %d tokens", input_tokens, output_tokens
        )
    except Exception:
        pass

    if not raw:
        raise ValueError("Empty response from Claude API")

    # JSON 部分を抽出（コードブロック対応）
    # ```json ... ``` または ```...``` を除去
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    # { ... } を抽出
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)

    if not raw:
        raise ValueError("No JSON found in Claude response")

    try:
        article = json.loads(raw)
    except json.JSONDecodeError as e:
        # 末尾が切れている場合の簡易補完
        logger.warning("JSON parse failed (%s), attempting repair", e)
        # bodyが途中で切れた場合に閉じる
        repaired = raw
        if repaired.count('"') % 2 != 0:
            repaired += '"'
        # 未閉の配列
        open_brackets = repaired.count("[") - repaired.count("]")
        repaired += "]" * max(0, open_brackets)
        # 未閉のオブジェクト
        open_braces = repaired.count("{") - repaired.count("}")
        repaired += "}" * max(0, open_braces)
        article = json.loads(repaired)

    # 必須キーの保証
    title_fallback = topic.get("title", "無題")
    article.setdefault("title_a", title_fallback)
    article.setdefault("title_b", title_fallback)
    article.setdefault("hashtags", [])
    article.setdefault("body", "")
    return article


def _build_template_body(
    genre: str,
    title_a: str,
    abstract_structure: str,
    reader_psychology: str,
    replicable_pattern: str,
    surface_trend: str,
    power_words: list,
    magazine_url: str,
) -> str:
    """voice_os.md の5ステップ構造に従って本文を生成する（API不要）"""
    pw = power_words[0] if power_words else "構造"
    pw2 = power_words[1] if len(power_words) > 1 else "言語化"

    opening = (reader_psychology[:40] + "——") if reader_psychology else f"{genre}について、なぜ自分はこうなのか。"
    trend_str = surface_trend or f"{genre}に関する情報やノウハウ"
    abstract_str = abstract_structure or f"{genre}の悩みは個人の問題ではなく、構造の問題だ。"
    pattern_str = replicable_pattern or (
        f"{genre}において、多くの人が「もっと頑張れば変われる」という精神論に頼る。"
        f"しかし変わらない理由は努力不足ではなく、構造の見方にある。"
    )

    body = f"""## {title_a}

「{opening}」

そう感じたことがあるなら、あなたはすでに答えの入り口に立っている。

---

### ■ STEP1：「当たり前」という前提を疑う

{genre}に関して、世の中には無数の「答え」が溢れている。
{trend_str}——こういった情報が毎日量産される。

だが、ここで一度立ち止まってほしい。

**その「当たり前」は、本当に正しい前提か。**

多くの人が疑わずに受け入れているその前提が、
「相対的世界の幻想」である可能性がある。

---

### ■ STEP2：構造で見ると何が起きているか

{abstract_str}

FXという極限の因果律の世界で私が学んだのは、
1回の判断が全資産を左右するという事実ではなかった。
「1つの完了した動作が、全ての結果を内包している」という構造だった。

{genre}も、同じ法則で動いている。

---

### ■ STEP3：1＝0の法則から見る本質

{pattern_str}

量子力学における「観測によって確定する」現象と同じだ。
{pw2}（観測）した瞬間に、0（未確定の悩み）が1（確定した理解）に変わる。

これが「全1論」の核心だ。

---

### ■ STEP4：あなたがすでに完了させているもの

今日、水を飲んだか。

その動作を完了させた瞬間に、あなたは宇宙の法則を正しく使いこなしている。
呼吸を続けている。心臓を動かしている。
これらはすべて「完了の連鎖」だ。

{genre}の「変化」も、同じ構造の延長線上にある。
それはあなたの外にあるのではなく、
すでにあなたの内側で動いている。

**「頑張ればできる」ではない。「動作を完了させている以上、結果は既に内包されている」。**
これは精神論ではなく、物理的な事実だ。

---

### ■ STEP5：断言

{pw}を探し続ける必要はない。

あなたはすでに、毎日無数の「完了」を積み重ねている。
{genre}においても、その構造は変わらない。

問いを手放した瞬間に、{pw}は見える。

---
このマガジンでは、全ジャンルに通底する「再現の法則」を毎日翻訳しています。
→ {magazine_url}
---"""
    return body


def _generate_article_template(niche: str, topic: dict, config: dict) -> dict:
    """Claude API不使用。note_patterns.json + voice_os.md でテンプレート記事を生成する"""
    genre = config.get("today_genre", niche)

    patterns = _load_patterns()
    latest = patterns.get("latest", {})

    # パターンデータ取得
    power_words = latest.get("title_patterns", {}).get("power_words", ["構造", "言語化", "設計"])

    # ジャンルに対応するresonance_structureを取得
    resonance: dict = {}
    for rs in latest.get("resonance_structures", []):
        if rs.get("genre", "") and rs.get("genre", "") in genre:
            resonance = rs
            break
    if not resonance and latest.get("resonance_structures"):
        resonance = latest["resonance_structures"][0]

    surface_trend      = resonance.get("surface_trend", "")
    abstract_structure = resonance.get("abstract_structure", "")
    reader_psychology  = resonance.get("reader_psychology", "")
    replicable_pattern = resonance.get("replicable_pattern", "")

    pw  = power_words[0] if power_words else "構造"
    pw2 = power_words[1] if len(power_words) > 1 else "言語化"

    # ジャンル別タイトルパターン（voice_os.mdの「なぜ〇〇は〜〜なのか」型）
    genre_title_map: dict[str, tuple[str, str]] = {
        "恋愛":   (f"なぜ{pw}を知ると恋愛が変わるのか",      f"好きな人に振り回される人の{pw}"),
        "婚活":   (f"婚活で疲れる人の{pw}を暴く",            f"なぜ婚活ほど{pw}が決め手になるのか"),
        "人間関係":(f"人間関係が消耗する本当の{pw}",          f"なぜ距離を置くほど孤立するのか"),
        "自己成長":(f"自己肯定感を高めようとする人の矛盾",    f"なぜ頑張るほど変われないのか"),
        "哲学":   (f"「普通」という言葉の{pw}を疑う",         f"なぜ{pw2}できないと不安になるのか"),
        "不安":   (f"不安が消えない人の{pw}の正体",           f"なぜ安心しようとするほど不安になるのか"),
        "孤独":   (f"孤独を癒そうとする人ほど孤独になる{pw}", f"孤独の{pw}を{pw2}する"),
        "習慣":   (f"習慣化が続かない人の{pw}を暴く",         f"なぜ意志力に頼るほど習慣は崩れるのか"),
    }
    default_titles = (f"なぜ{genre}に悩む人ほど{pw}を見失うのか", f"{genre}が変わらない本当の{pw}")
    title_a, title_b = genre_title_map.get(genre, default_titles)

    magazine_url = (
        f"https://note.com/{config.get('note_user_id', 'militech_2077')}"
        f"/m/{config.get('magazine_id', '')}"
    )

    body = _build_template_body(
        genre=genre,
        title_a=title_a,
        abstract_structure=abstract_structure,
        reader_psychology=reader_psychology,
        replicable_pattern=replicable_pattern,
        surface_trend=surface_trend,
        power_words=power_words,
        magazine_url=magazine_url,
    )

    return {
        "title_a": title_a,
        "title_b": title_b,
        "body": body,
        "hashtags": [genre, pw, pw2, "言語化", "全1論"][:5],
        "generated_by": "template",
    }


def _save_ready_md(article: dict, ready_dir: Path, today: str) -> Path:
    """ready/YYYY-MM-DD.md に記事を保存する（同日複数の場合は連番）"""
    ready_dir.mkdir(parents=True, exist_ok=True)

    generated_by = article.get("generated_by", "claude_api")
    mode_label = "【テンプレート生成】" if generated_by == "template" else "【Claude API生成】"
    hashtags_str = " ".join(f"#{h}" for h in article.get("hashtags", []))

    content = (
        f"# {article['title_a']}\n\n"
        f"{mode_label} 生成日: {today}\n\n"
        f"---\n\n"
        f"**タイトルB案:** {article['title_b']}\n\n"
        f"**ハッシュタグ:** {hashtags_str}\n\n"
        f"---\n\n"
        f"{article['body']}\n"
    )

    ready_path = ready_dir / f"{today}.md"
    counter = 1
    while ready_path.exists():
        ready_path = ready_dir / f"{today}_{counter:02d}.md"
        counter += 1

    ready_path.write_text(content, encoding="utf-8")
    logger.info("Saved ready: %s (mode=%s)", ready_path.name, generated_by)
    return ready_path


def run_creator(
    config: dict,
    data_dir: Path,
    topics: list,
    abstraction_meta: dict | None = None,
) -> list[dict]:
    """
    トピックリストから記事を生成し、下書きJSONと ready/YYYY-MM-DD.md に保存する。

    API利用可能時 : Claude API で高品質記事生成
    API利用不可時 : note_patterns.json + voice_os.md でテンプレート記事生成
    """
    niche = config.get("niche", "副業・節税")
    model = config.get("model", "claude-sonnet-4-6")
    articles_per_day = config.get("articles_per_day", 1)

    # ── API利用可否を確認 ────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = None
    if api_key:
        try:
            client = anthropic.Anthropic(api_key=api_key)
        except Exception as exc:
            logger.warning("Anthropic client 初期化失敗: %s", exc)

    drafts_dir = data_dir / "data" / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    ready_dir = data_dir / "ready"

    today = date.today().isoformat()
    results = []

    for topic in topics[:articles_per_day]:
        article = None
        generated_by = "template"

        # ── API生成を試みる ──────────────────────────────────────────────────
        if client:
            try:
                article = _generate_article(client, model, niche, topic, config, abstraction_meta)
                generated_by = "claude_api"
                logger.info("Claude API 生成成功: %s", article.get("title_a", ""))
            except Exception as exc:
                logger.warning("API生成失敗 → テンプレートフォールバック: %s", exc)

        # ── テンプレートフォールバック ────────────────────────────────────────
        if article is None:
            logger.info("テンプレートベース記事生成を開始 (genre=%s)...", config.get("today_genre", niche))
            article = _generate_article_template(niche, topic, config)

        # ── アフィリエイト挿入 ────────────────────────────────────────────────
        today_genre = config.get("today_genre", niche)
        topic_title = topic.get("title", "")
        matched_affiliates = _select_affiliates(today_genre, topic_title)
        body_with_affiliates = _append_affiliate_section(article["body"], matched_affiliates)
        article["body"] = body_with_affiliates
        article["generated_by"] = generated_by

        # ── drafts/ に JSON 保存 ──────────────────────────────────────────────
        slug = _slugify(article["title_a"])
        draft_path = drafts_dir / f"{today}_{slug}.json"
        draft = {
            "title_a": article["title_a"],
            "title_b": article["title_b"],
            "body": body_with_affiliates,
            "hashtags": article["hashtags"],
            "topic": topic,
            # name と url を両方保存（Discord送信時の関連広告リンク生成に使用）
            "affiliates_inserted": [
                {"name": af.get("name", ""), "url": af.get("url", "")}
                for af in matched_affiliates
            ],
            "generated_by": generated_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
        logger.info("Saved draft: %s", draft_path.name)

        # ── ready/ に Markdown 保存 ───────────────────────────────────────────
        ready_path = _save_ready_md(article, ready_dir, today)

        results.append({
            "path": str(draft_path),
            "ready_path": str(ready_path),
            "title_a": draft["title_a"],
            "title_b": draft["title_b"],
            "hashtags": draft["hashtags"],
            "generated_by": generated_by,
            "created_at": draft["created_at"],
        })

    return results
