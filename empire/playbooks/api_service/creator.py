"""
Creator: Claude で FastAPI サービスコードを生成し app/ ディレクトリに保存する
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


def _ask_claude(client: anthropic.Anthropic, model: str, prompt: str, max_tokens: int = 4096) -> str:
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _extract_block(text: str, lang: str = "") -> str:
    """コードブロック (```lang ... ```) を抽出する。見つからない場合はそのまま返す。"""
    pattern = rf"```{lang}\n(.*?)```" if lang else r"```(?:\w+)?\n(.*?)```"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1) if m else text


def _generate_fastapi_app(client: anthropic.Anthropic, model: str, opportunity: dict) -> dict[str, str]:
    """FastAPI アプリの各ファイルを Claude で生成して辞書で返す。"""
    name = opportunity["name"]
    description = opportunity["description"]
    endpoints = opportunity.get("endpoint_examples", ["POST /analyze"])

    prompt = f"""あなたはPython/FastAPIエキスパートです。
以下のAPI仕様に基づいて、完全に動作するFastAPIサービスの `main.py` を生成してください。

【サービス名】{name}
【説明】{description}
【エンドポイント例】{', '.join(endpoints)}

【要件】
- GET / : APIの基本情報を返す（name, version, description, endpoints）
- POST /analyze（または仕様に応じた主要エンドポイント）: リクエストボディを受け取り、anthropic ライブラリ経由でClaude APIを呼び出して結果を返す
- シンプルなインメモリレート制限（1IPあたり1分間に60リクエスト）
- X-API-Key ヘッダーによる認証（環境変数 API_KEY と照合、未設定なら認証スキップ）
- Pydantic モデルで入出力を定義
- 適切なエラーハンドリングとJSONレスポンス
- ポートは環境変数 PORT（デフォルト8000）を使用

Pythonコードのみを ```python ... ``` ブロックで返してください。説明文は不要です。"""

    main_py_raw = _ask_claude(client, model, prompt)
    main_py = _extract_block(main_py_raw, "python")

    requirements = (
        "anthropic>=0.25.0\n"
        "fastapi>=0.110.0\n"
        "uvicorn[standard]>=0.29.0\n"
        "pydantic>=2.0.0\n"
        "python-dotenv>=1.0.0\n"
    )

    dockerfile = f"""FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
ENV PORT=8000
EXPOSE $PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
"""

    readme = f"""# {name}

{description}

## Quick Start

```bash
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY を設定
docker build -t {name} .
docker run -p 8000:8000 --env-file .env {name}
```

## Endpoints

{chr(10).join(f'- `{ep}`' for ep in endpoints)}
- `GET /` — API情報

## Authentication

リクエストヘッダに `X-API-Key` を含めてください（環境変数 `API_KEY` が設定されている場合）。

## Example

```bash
curl -X POST http://localhost:8000/analyze \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: your_key" \\
  -d '{{"text": "分析したいテキスト"}}'
```

## Pricing (RapidAPI)

- Basic: 無料 100リクエスト/月
- Pro: $9.99/月 無制限
"""

    env_example = "ANTHROPIC_API_KEY=your_anthropic_key_here\nAPI_KEY=your_api_key_here\nPORT=8000\n"

    return {
        "main.py": main_py,
        "requirements.txt": requirements,
        "Dockerfile": dockerfile,
        "README.md": readme,
        ".env.example": env_example,
    }


def _generate_rapidapi_listing(client: anthropic.Anthropic, model: str, opportunity: dict) -> dict:
    name = opportunity["name"]
    description = opportunity["description"]
    prompt = f"""RapidAPI のAPIリスティング情報を以下のJSONで返してください（JSONのみ）:
{{
  "title": "{name} の英語タイトル（50字以内）",
  "description": "英語の説明文（500字以内）",
  "category": "最適なRapidAPIカテゴリ",
  "pricing": {{
    "basic": "Free — 100 requests/month",
    "pro": "$9.99/month — Unlimited requests"
  }},
  "tags": ["tag1", "tag2", "tag3"]
}}
サービス説明: {description}"""

    raw = _ask_claude(client, model, prompt, max_tokens=512)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {
        "title": name,
        "description": description,
        "category": "Text Analysis",
        "pricing": {"basic": "Free — 100 req/month", "pro": "$9.99/month"},
        "tags": [],
    }


def run_creator(config: dict, data_dir: Path, opportunities: list) -> list[dict]:
    """トップ1件の機会についてFastAPIサービスを生成し、app/に保存する。"""
    if not opportunities:
        logger.info("No opportunities to create service for.")
        return []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = config.get("model", "claude-sonnet-4-6")

    opportunity = opportunities[0]
    name = opportunity["name"]
    logger.info("Creating service for: %s", name)

    # ファイル生成
    files = _generate_fastapi_app(client, model, opportunity)
    rapidapi_listing = _generate_rapidapi_listing(client, model, opportunity)

    # app/ に保存
    app_dir = data_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        file_path = app_dir / filename
        file_path.write_text(content, encoding="utf-8")
        logger.info("Written: %s", file_path)

    created_at = datetime.now(timezone.utc).isoformat()
    service = {
        "name": name,
        "app_dir": str(app_dir),
        "rapidapi_listing": rapidapi_listing,
        "endpoints": opportunity.get("endpoint_examples", []),
        "created_at": created_at,
        "status": "created",
    }

    # services.jsonl に追記
    services_file = data_dir / "data" / "services.jsonl"
    with services_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(service, ensure_ascii=False) + "\n")

    logger.info("Service '%s' created and saved.", name)
    return [service]
