#!/usr/bin/env python3
"""
承認済み10本のZenn記事を生成するワンタイムスクリプト
"""
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

# Approved titles for Zenn articles
APPROVED_TOPICS = [
    "副業で年10万稼いでも確定申告しなくていいケースと必要なケース",
    "iDeCoを満額積んだら手取りがどう変わったか：実数計算",
    "会社員が副業を始める前に知るべき「住民税バレる仕組み」",
    "フリマアプリの売上に税金がかかるラインと申告不要のケース",
    "青色申告65万控除を取るために最低限やること3つ",
    "ふるさと納税と副業収入の組み合わせ：控除上限の正しい計算",
    "副業の赤字を給与から引ける場合・引けない場合の違い",
    "副業専用クレジットカードの経費処理と記帳のやり方",
    "社会保険の壁：副業収入が増えると保険料はどう変わるか",
    "個人事業主のまま vs 法人化：年収いくらで法人化すべきか",
]

sys.path.insert(0, str(Path(__file__).parent))

import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

VOICE_OS_PATH = Path("thoughts/voice_os.md")
HUMAN_WRITING_OS_PATH = Path("thoughts/human_writing_os.md")

def load_os(path):
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""

def build_system_prompt():
    voice_os = load_os(VOICE_OS_PATH)
    human_os = load_os(HUMAN_WRITING_OS_PATH)
    parts = [
        "あなたは「ミリテク」のゴーストライターAIです。",
        "以下の2つのOSを同時に体現して記事を書いてください。",
        "",
    ]
    if voice_os:
        parts += ["## [OS-1] 著者の思考OS（全1論・一即全・100%再現論）", voice_os, ""]
    if human_os:
        parts += ["## [OS-2] 文体OS（生活の解像度が高い個人ブロガー）", human_os, ""]
    return "\n".join(parts)

def build_prompt(topic):
    return f"""以下のトピックでZenn記事を書いてください。

## 執筆条件
トピック: {topic}
ジャンル: 副業・節税・お金
重点キーワード: 副業、確定申告、節税、所得税、住民税
文字数: 2500〜4000字

## タイトルについて
トピックのタイトルをそのまま使用すること（変更不要）

## 出力フォーマット（厳守）
まず以下のJSONを1行で出力（コードブロック不要）:
{{"title": "{topic}", "emoji": "絵文字1字", "topics": ["sidejob", "tax", "money"], "score": 85, "reason": "具体的数字で信頼性高"}}

次の行から記事本文（Markdownのみ・フロントマター不要・## 見出しから始める）:

必須要素:
- 冒頭は具体的な体験か感情から始める（絵文字禁止）
- 具体的な数字・計算例を必ず入れる
- 会社員視点で書く
- 末尾のCTA:
---
副業・節税の情報をもっと詳しく → noteマガジン「言語化の技術」で毎日更新中。
https://note.com/militech_2077/m/mf82e085b93c9
---"""

articles_dir = Path("articles")
articles_dir.mkdir(exist_ok=True)

system = build_system_prompt()
today = date.today().isoformat()

existing = set()
for f in articles_dir.glob("*.md"):
    existing.add(f.stem)

print(f"既存記事数: {len(list(articles_dir.glob('*.md')))}")
generated = 0

for i, topic in enumerate(APPROVED_TOPICS):
    print(f"\n[{i+1}/10] 生成中: {topic[:40]}...")
    
    slug = hashlib.md5(topic.encode()).hexdigest()[:8]
    filename = f"{today.replace('-', '')}-{slug}.md"
    filepath = articles_dir / filename
    
    if filepath.exists():
        print(f"  → スキップ（既存）: {filename}")
        continue
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": build_prompt(topic)}],
        )
        content = response.content[0].text.strip()
        
        # Parse JSON line 1
        lines = content.split("\n")
        meta_line = lines[0].strip()
        body_lines = lines[1:]
        
        # Extract JSON
        import re
        json_match = re.search(r'\{.*\}', meta_line)
        if json_match:
            meta = json.loads(json_match.group())
        else:
            meta = {"title": topic, "emoji": "💰", "topics": ["sidejob", "tax", "money"]}
        
        title = meta.get("title", topic)
        emoji = meta.get("emoji", "💰")
        topics = meta.get("topics", ["sidejob", "tax", "money"])[:3]
        
        body = "\n".join(body_lines).strip()
        
        # Write Zenn article
        frontmatter = f"""---
title: "{title}"
emoji: "{emoji}"
type: "idea"
topics: {json.dumps(topics, ensure_ascii=False)}
published: true
---

"""
        filepath.write_text(frontmatter + body, encoding="utf-8")
        print(f"  ✅ 生成完了: {filename}")
        generated += 1
        
    except Exception as e:
        print(f"  ❌ エラー: {e}")

print(f"\n完了: {generated}本生成, 合計{len(list(articles_dir.glob('*.md')))}本")
