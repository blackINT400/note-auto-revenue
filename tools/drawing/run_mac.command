#!/bin/bash
# MT4オーバーレイ描画ツール（Mac）をダブルクリックで起動するためのランチャー。
# 初回は pyobjc を自動インストールします（費用ゼロ）。
cd "$(dirname "$0")" || exit 1

echo "▶ 依存ライブラリ(pyobjc)を確認しています..."
python -c "import Cocoa" 2>/dev/null || {
    echo "▶ pyobjc をインストールします（初回のみ・数十秒）..."
    python -m pip install --quiet --upgrade pyobjc-framework-Cocoa || {
        echo "✕ インストールに失敗しました。ターミナルで次を実行してください:"
        echo "    pip install pyobjc-framework-Cocoa"
        read -r -p "Enterキーで閉じます..."
        exit 1
    }
}

echo "▶ 起動します。上部のツールバーで操作してください（✕で終了）"
exec python mt4_overlay_mac.py
