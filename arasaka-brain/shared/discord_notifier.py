"""
Discord Webhook 通知
"""
import os
import json
import urllib.request
import urllib.error
from datetime import datetime


class DiscordNotifier:
    def __init__(self, discord_config: dict):
        self.webhook_url = os.path.expandvars(discord_config.get("webhook_url", ""))
        channels = discord_config.get("channels", {})
        self.report_channel = os.path.expandvars(channels.get("report", ""))
        self.alert_channel = os.path.expandvars(channels.get("alert", ""))

    def _send(self, webhook_url: str, payload: dict) -> bool:
        if not webhook_url or webhook_url.startswith("$"):
            print(f"[Discord] webhook未設定 — メッセージ: {payload.get('content', '')[:100]}")
            return False
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 204)
        except urllib.error.URLError as e:
            print(f"[Discord] 送信失敗: {e}")
            return False

    def report(self, result: dict) -> bool:
        url = self.report_channel or self.webhook_url
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"**[ARASAKA REPORT] {ts}**"]
        for k, v in result.items():
            lines.append(f"• {k}: {v}")
        return self._send(url, {"content": "\n".join(lines)})

    def alert(self, message: str) -> bool:
        url = self.alert_channel or self.webhook_url
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self._send(url, {"content": f"🚨 **[ARASAKA ALERT] {ts}**\n{message}"})

    def sovereign_request(self, title: str, details: dict) -> bool:
        url = self.alert_channel or self.webhook_url
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"⚔️ **[総帥承認要求] {ts}**", f"**{title}**"]
        for k, v in details.items():
            lines.append(f"• {k}: {v}")
        lines.append("\n✅ 承認する場合は手動でステータスを変更してください")
        return self._send(url, {"content": "\n".join(lines)})
