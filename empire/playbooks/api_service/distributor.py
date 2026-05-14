"""
Distributor: デプロイパッケージを作成し、オプションでSSHデプロイを実行する
"""
import json
import logging
import os
import subprocess
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _zip_app_dir(app_dir: Path, dest_zip: Path) -> bool:
    """app/ ディレクトリを ZIP に圧縮する。"""
    if not app_dir.exists():
        logger.warning("app_dir does not exist: %s", app_dir)
        return False
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in app_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(app_dir))
    logger.info("Package created: %s", dest_zip)
    return True


def _generate_deploy_guide(service_name: str, dest: Path) -> None:
    """デプロイ手順書 DEPLOY_GUIDE.md を生成する。"""
    content = f"""# {service_name} デプロイガイド

## 1. サーバー準備

### さくらVPS（月220円〜）
```bash
# VPS 初期設定後、SSH でログイン
ssh root@your-vps-ip

# Docker インストール（Ubuntu 22.04）
curl -fsSL https://get.docker.com | sh
```

### Render.com 無料枠
1. https://render.com にアクセスしてアカウント作成
2. New > Web Service を選択
3. GitHub リポジトリを接続（Dockerfile が自動検出されます）
4. Environment Variables に `ANTHROPIC_API_KEY` を設定

---

## 2. ローカル / VPS での Docker 実行

```bash
# ZIP を解凍
unzip deploy_{service_name}_{date.today()}.zip -d {service_name}
cd {service_name}

# .env ファイルを作成
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY を設定

# Docker ビルド & 起動
docker build -t {service_name} .
docker run -d --name {service_name} -p 8000:8000 --env-file .env {service_name}

# 動作確認
curl http://localhost:8000/
```

---

## 3. RapidAPI への登録手順

1. https://rapidapi.com/provider にアクセス
2. "Add New API" をクリック
3. API の基本情報を入力
   - Name: サービス名
   - Category: 適切なカテゴリを選択
   - Base URL: `http://your-server-ip:8000`
4. エンドポイントを設定
   - POST /analyze（またはメインエンドポイント）
   - GET /（ヘルスチェック）
5. Pricing Plans を設定
   - Basic: Free（100 requests/month）
   - Pro: $9.99/month（Unlimited）
6. "Test Endpoints" でエンドポイントが正しく動作することを確認
7. "Publish" をクリックして公開

---

## 4. 環境変数の設定

| 変数名 | 説明 | 必須 |
|--------|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API キー | ✅ |
| `API_KEY` | X-API-Key 認証用キー（省略可）| ❌ |
| `PORT` | ポート番号（デフォルト: 8000）| ❌ |

---

## 5. 監視・ログ確認

```bash
# コンテナログ確認
docker logs {service_name} -f

# コンテナ再起動
docker restart {service_name}
```

---

## 6. アップデート手順

```bash
docker stop {service_name} && docker rm {service_name}
docker build -t {service_name} .
docker run -d --name {service_name} -p 8000:8000 --env-file .env {service_name}
```
"""
    dest.write_text(content, encoding="utf-8")
    logger.info("Deploy guide written: %s", dest)


def _ssh_deploy(vps_host: str, service_name: str, package_path: Path) -> bool:
    """SSH 経由でリモートサーバーにデプロイする。"""
    try:
        # ディレクトリ作成 & 旧コンテナ停止・削除
        prep_cmd = (
            f"mkdir -p ~/services/{service_name} && "
            f"docker stop {service_name} 2>/dev/null; "
            f"docker rm {service_name} 2>/dev/null; true"
        )
        result = subprocess.run(
            ["ssh", vps_host, prep_cmd],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode not in (0, 1):
            logger.warning("SSH prep returned code %d: %s", result.returncode, result.stderr)

        # ZIP 転送
        scp_result = subprocess.run(
            ["scp", str(package_path), f"{vps_host}:~/services/{service_name}/deploy.zip"],
            capture_output=True, text=True, timeout=120,
        )
        if scp_result.returncode != 0:
            logger.error("SCP failed: %s", scp_result.stderr)
            return False

        # 解凍 & ビルド & 起動
        deploy_cmd = (
            f"cd ~/services/{service_name} && "
            f"unzip -o deploy.zip && "
            f"docker build -t {service_name} . && "
            f"docker run -d --name {service_name} -p 8000:8000 --env-file .env {service_name}"
        )
        run_result = subprocess.run(
            ["ssh", vps_host, deploy_cmd],
            capture_output=True, text=True, timeout=300,
        )
        if run_result.returncode != 0:
            logger.error("Remote deploy failed: %s", run_result.stderr)
            return False

        logger.info("SSH deploy succeeded for %s on %s", service_name, vps_host)
        return True
    except subprocess.TimeoutExpired:
        logger.error("SSH deploy timed out for %s", service_name)
        return False
    except FileNotFoundError:
        logger.warning("ssh/scp command not found — skipping remote deploy")
        return False


def _update_service_status(services_file: Path, service_name: str, extra: dict) -> None:
    """services.jsonl の該当レコードに追加情報を書き込む。"""
    lines = []
    if services_file.exists():
        for line in services_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("name") == service_name:
                    obj.update(extra)
                lines.append(json.dumps(obj, ensure_ascii=False))
            except json.JSONDecodeError:
                lines.append(line)
    services_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_distributor(config: dict, data_dir: Path, services: list) -> list[dict]:
    """各サービスのデプロイパッケージを作成し、必要に応じてSSHデプロイを実行する。"""
    results = []
    app_dir = data_dir / "app"
    data_subdir = data_dir / "data"
    vps_host = os.environ.get("VPS_HOST", "")

    for service in services:
        name = service.get("name", "api_service")
        main_py = app_dir / "main.py"
        if not main_py.exists():
            logger.warning("main.py not found in %s — skipping %s", app_dir, name)
            results.append({"name": name, "status": "skipped_no_app", "deploy_guide_path": "", "package_path": ""})
            continue

        # ZIP パッケージ作成
        zip_name = f"deploy_{name}_{date.today()}.zip"
        package_path = data_subdir / zip_name
        zip_ok = _zip_app_dir(app_dir, package_path)

        # デプロイガイド生成
        guide_path = data_subdir / "DEPLOY_GUIDE.md"
        _generate_deploy_guide(name, guide_path)

        # SSH デプロイ（VPS_HOST が設定されている場合）
        ssh_status = "not_attempted"
        if vps_host and zip_ok:
            success = _ssh_deploy(vps_host, name, package_path)
            ssh_status = "deployed" if success else "deploy_failed"

        status = ssh_status if vps_host else ("packaged" if zip_ok else "package_failed")

        result = {
            "name": name,
            "status": status,
            "deploy_guide_path": str(guide_path),
            "package_path": str(package_path) if zip_ok else "",
            "deployed_at": datetime.now(timezone.utc).isoformat(),
        }

        # services.jsonl を更新
        services_file = data_subdir / "services.jsonl"
        _update_service_status(services_file, name, {
            "status": status,
            "package_path": result["package_path"],
            "deploy_guide_path": result["deploy_guide_path"],
            "deployed_at": result["deployed_at"],
        })

        results.append(result)
        logger.info("Distributor result: %s", result)

    return results
