"""
Analyzer: リクエストログの解析、RapidAPI メトリクス取得、Claude による推奨事項生成
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


def _read_request_logs(requests_dir: Path) -> list[dict]:
    """data/requests/ 以下の YYYY-MM-DD.jsonl を読み込む。"""
    logs = []
    if not requests_dir.exists():
        return logs
    for log_file in sorted(requests_dir.glob("*.jsonl")):
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return logs


def _calc_metrics(logs: list[dict]) -> dict:
    """ログリストから基本メトリクスを計算する。"""
    if not logs:
        return {
            "total_requests_month": 0,
            "avg_response_time_ms": 0.0,
            "error_rate": 0.0,
            "endpoint_counts": {},
        }

    # 今月分のみ
    now = datetime.now(timezone.utc)
    ym = (now.year, now.month)
    month_logs = []
    for entry in logs:
        ts_raw = entry.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if (ts.year, ts.month) == ym:
                month_logs.append(entry)
        except Exception:
            month_logs.append(entry)  # タイムスタンプ不正なら全件含める

    total = len(month_logs)
    errors = sum(1 for e in month_logs if int(e.get("status_code", 200)) >= 400)
    response_times = [float(e["response_time_ms"]) for e in month_logs if "response_time_ms" in e]
    avg_rt = sum(response_times) / len(response_times) if response_times else 0.0

    endpoint_counts: dict[str, int] = {}
    for e in month_logs:
        ep = e.get("endpoint", "unknown")
        endpoint_counts[ep] = endpoint_counts.get(ep, 0) + 1

    return {
        "total_requests_month": total,
        "avg_response_time_ms": round(avg_rt, 2),
        "error_rate": round(errors / total, 4) if total else 0.0,
        "endpoint_counts": endpoint_counts,
    }


def _fetch_rapidapi_analytics(rapidapi_key: str) -> dict:
    """RapidAPI の非公式アナリティクスエンドポイントを試みる（失敗時は空辞書）。"""
    if not rapidapi_key:
        return {}
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://rapidapi.com/developer/analytics",
            headers={
                "X-RapidAPI-Key": rapidapi_key,
                "User-Agent": "Mozilla/5.0 (compatible; AnalyzerBot/1.0)",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        logger.info("RapidAPI analytics fetched.")
        return data
    except Exception as exc:
        logger.warning("RapidAPI analytics fetch failed: %s", exc)
        return {}


def _count_services(data_dir: Path) -> int:
    services_file = data_dir / "data" / "services.jsonl"
    if not services_file.exists():
        return 0
    return sum(
        1 for line in services_file.read_text().splitlines()
        if line.strip() and json.loads(line.strip()).get("status") not in ("skipped_no_app",)
    )


def _ask_claude_analysis(
    client: anthropic.Anthropic,
    model: str,
    metrics: dict,
    endpoint_counts: dict,
    service_count: int,
) -> dict:
    """Claude にパターン分析と推奨事項を依頼する。"""
    prompt = f"""あなたはAPIビジネスのアナリストです。
以下のメトリクスを分析して、JSON形式で推奨事項を返してください。

【メトリクス】
- 今月のリクエスト数: {metrics['total_requests_month']}
- 平均レスポンスタイム: {metrics['avg_response_time_ms']} ms
- エラーレート: {metrics['error_rate'] * 100:.1f}%
- エンドポイント別リクエスト数: {json.dumps(endpoint_counts, ensure_ascii=False)}
- デプロイ済みサービス数: {service_count}

以下のJSONのみ返してください（説明文なし）:
{{
  "pricing_recommendation": "料金プランの調整提案（50字以内）",
  "new_endpoint_suggestions": ["提案エンドポイント1", "提案エンドポイント2"],
  "performance_note": "パフォーマンスに関するコメント（50字以内）",
  "growth_action": "成長のための次のアクション（50字以内）"
}}"""

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as exc:
        logger.warning("Claude analysis failed: %s", exc)
    return {
        "pricing_recommendation": "現行プランを維持",
        "new_endpoint_suggestions": [],
        "performance_note": "データ不足",
        "growth_action": "リクエスト数を増やすためにマーケティングを強化",
    }


def run_analyzer(config: dict, data_dir: Path) -> dict:
    """ログ解析 + RapidAPI メトリクス + Claude 推奨事項をまとめて返す。"""
    requests_dir = data_dir / "data" / "requests"
    logs = _read_request_logs(requests_dir)
    metrics = _calc_metrics(logs)

    rapidapi_key = os.environ.get("RAPIDAPI_KEY", "")
    rapidapi_data = _fetch_rapidapi_analytics(rapidapi_key)

    service_count = _count_services(data_dir)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = config.get("model", "claude-sonnet-4-6")

    recommendations = _ask_claude_analysis(
        client, model, metrics, metrics["endpoint_counts"], service_count
    )

    # スケールトリガー
    scale_trigger = metrics["total_requests_month"] >= 100

    # 収益推定
    price_per_req = config.get("price_per_request_jpy", 0.1)
    estimated_revenue_jpy = round(metrics["total_requests_month"] * price_per_req, 2)

    result = {
        "total_requests": metrics["total_requests_month"],
        "avg_response_time": metrics["avg_response_time_ms"],
        "error_rate": metrics["error_rate"],
        "estimated_revenue_jpy": estimated_revenue_jpy,
        "service_count": service_count,
        "recommendations": recommendations,
        "scale_trigger": scale_trigger,
        "rapidapi_data": rapidapi_data,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    # 保存
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    out_path = data_dir / "data" / f"analysis_{ym}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    logger.info("Analysis saved to %s", out_path)

    return result
