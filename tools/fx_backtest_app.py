"""FX自動バックテスター — Streamlit App
起動: streamlit run tools/fx_backtest_app.py
"""
from __future__ import annotations
import io, json, sys, os

# tools/ をパスに追加（どこから実行しても動くように）
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from anthropic import Anthropic

from fx_backtest_engine import (
    parse_strategy, fetch_data, run_backtest, calculate_statistics
)

# ── ページ設定 ────────────────────────────────────────────────
st.set_page_config(
    page_title="FX自動バックテスター",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* KPI values larger */
[data-testid="stMetricValue"] { font-size: 1.6rem !important; }
/* Parsed pills */
.pill {
    display: inline-block;
    background: rgba(255,215,0,.12);
    border: 1px solid rgba(255,215,0,.35);
    border-radius: 14px;
    padding: 3px 10px;
    margin: 2px;
    font-size: .8rem;
    color: #ffd700;
}
</style>
""", unsafe_allow_html=True)

# ── サイドバー ────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ FX バックテスター")
    st.divider()

    pip_value = st.number_input(
        "1pip あたり損益 (JPY)",
        min_value=100, max_value=500_000, value=1_000, step=100,
        help="¥1,000 ≒ ドル円 0.1 ロット相当",
    )

    st.divider()
    st.markdown("**対応通貨ペア**")
    st.caption(
        "USDJPY / EURUSD / GBPUSD / GBPJPY\n"
        "AUDUSD / AUDJPY / EURJPY / NZDUSD\n"
        "USDCHF / XAUUSD（金） / BTCUSD"
    )
    st.divider()
    st.markdown("**対応インジケーター**")
    st.caption(
        "RSI · MA(SMA) · EMA\n"
        "MACD · ボリンジャーバンド · ストキャスティクス"
    )
    st.divider()
    st.caption(
        "データソース: yfinance（無料）\n"
        "1h足: 最大730日 ／ 日足: 制限なし"
    )

# ── メイン ────────────────────────────────────────────────────
st.title("⚡ FX 自動バックテスター")
st.caption("検証したい戦略を日本語で入力するだけ — Claude AI が解析して過去データで検証します")

EXAMPLES = {
    "🥇 ゴールド RSI 逆張り":
        "ゴールド（XAUUSD）4時間足で、RSI30以下で買い・RSI70以上で売り（ショートも同様）。"
        "SL30ドル、TP60ドル。2022年から2024年。",
    "💴 ドル円 EMA クロス":
        "ドル円1時間足で5EMAが20EMAを上抜いたらロング、下抜いたらショート。"
        "SL20pips、TP40pips。2023年1月から2024年12月。",
    "💶 ユーロドル MACD":
        "ユーロドル4時間足でMACDゴールデンクロスでロング、デッドクロスでショート。"
        "SL30pips、TP60pips。2022年から2024年。",
    "💷 ポンド円 ボリンジャー":
        "ポンド円1時間足で価格がBB下限を下抜けたら買い、上限を上抜けたら売り。"
        "SL40pips、TP80pips。2023年から2024年。",
    "₿ ビットコイン RSI":
        "BTCUSD日足でRSI30以下で買い、RSI70以上で売り。SL2000ドル、TP4000ドル。2020年から2024年。",
}

col_input, _ = st.columns([3, 1])
with col_input:
    ex_key = st.selectbox("クイック例（選択後に自由編集）",
                          ["（自分で入力）"] + list(EXAMPLES.keys()))
    default = EXAMPLES[ex_key] if ex_key != "（自分で入力）" else ""

user_input = st.text_area(
    "検証内容を日本語で入力",
    value=default, height=90,
    placeholder="例: ドル円1時間足でRSI30以下で買い、70以上で売り。SL20pips、TP40pips。2022年から2024年。",
)

run_btn = st.button("▶ バックテスト実行", type="primary", use_container_width=True)

# ── 実行 ─────────────────────────────────────────────────────
if run_btn and user_input.strip():
    st.session_state.pop("bt_results", None)

    with st.spinner("⚙️ Claude AI が戦略を解析中..."):
        try:
            client   = Anthropic()
            strategy = parse_strategy(user_input, client)
            strategy["pip_value"] = pip_value
        except Exception as e:
            st.error(f"戦略解析エラー: {e}")
            st.stop()

    # 解析結果のピル表示
    pills = [
        f"📌 {strategy['symbol']}",
        f"⏱ {strategy['timeframe']}",
        f"📅 {strategy['start_date']} → {strategy['end_date']}",
    ]
    if strategy.get("stop_loss_pips"):
        pills.append(f"🛑 SL {strategy['stop_loss_pips']} pips")
    if strategy.get("take_profit_pips"):
        pills.append(f"🎯 TP {strategy['take_profit_pips']} pips")
    for r in (strategy.get("entry_long") or []):
        v = f" {r['value']}" if r.get("value") is not None else ""
        pills.append(f"📈 Long: {r['indicator']} {r['condition']}{v}")
    for r in (strategy.get("entry_short") or []):
        v = f" {r['value']}" if r.get("value") is not None else ""
        pills.append(f"📉 Short: {r['indicator']} {r['condition']}{v}")

    st.success("✅ 戦略解析完了")
    st.markdown(
        " ".join(f'<span class="pill">{p}</span>' for p in pills),
        unsafe_allow_html=True,
    )
    if strategy.get("description"):
        st.caption(strategy["description"])
    st.divider()

    with st.spinner("📡 市場データ取得中..."):
        try:
            df = fetch_data(
                strategy["symbol"], strategy["timeframe"],
                strategy["start_date"], strategy["end_date"],
            )
            if df.attrs.get("warning"):
                st.warning(df.attrs["warning"])
            st.info(f"✅ {df.attrs['bars']:,} 本のローソク足データ取得完了")
        except Exception as e:
            st.error(f"データ取得エラー: {e}")
            st.stop()

    with st.spinner("🏃 バックテスト実行中..."):
        try:
            result = run_backtest(df, strategy)
            stats  = calculate_statistics(result, strategy)
        except Exception as e:
            st.error(f"バックテストエラー: {e}")
            st.stop()

    if not stats.get("total_trades"):
        st.warning(
            "⚠️ トレードが発生しませんでした。\n"
            "条件が厳しすぎるか、期間・時間足を変えて再試行してください。"
        )
        st.stop()

    st.session_state["bt_results"] = (result, stats, strategy)

# ── 結果表示 ─────────────────────────────────────────────────
if "bt_results" not in st.session_state:
    st.stop()

result, stats, strategy = st.session_state["bt_results"]
trades_df = pd.DataFrame(result["trades"])
pip_val   = stats["pip_value"]

# ── KPI 段1: 基本 ─────────────────────────────────────────────
r1 = st.columns(5)
r1[0].metric("トレード数",
             f'{stats["total_trades"]} 回',
             f'L:{stats["long_trades"]} / S:{stats["short_trades"]}')
r1[1].metric("勝率",
             f'{stats["win_rate"]} %',
             f'{stats["win_trades"]} 勝 {stats["loss_trades"]} 敗')
r1[2].metric("損益合計",
             f'{stats["net_pips"]:+.1f} pips',
             f'¥{stats["net_pnl_jpy"]:+,.0f}')
r1[3].metric("プロフィットファクター",
             str(stats["profit_factor"]) if stats["profit_factor"] else "∞",
             "1.0 以上で黒字")
r1[4].metric("最大ドローダウン",
             f'{stats["max_drawdown_pct"]:.2f} %',
             f'{stats["max_drawdown_pips"]:.0f} pips')

# ── KPI 段2: リスク調整済 ─────────────────────────────────────
r2 = st.columns(5)
r2[0].metric("シャープレシオ",   f'{stats["sharpe_ratio"]}',  "2.0↑ 優秀")
r2[1].metric("ソルティノレシオ", f'{stats["sortino_ratio"]}', "下方リスク重視")
r2[2].metric("カルマーレシオ",   f'{stats["calmar_ratio"]}',  "2.0↑ 理想")
r2[3].metric("SQN",              f'{stats["sqn"]}',           "2.5↑ Good / 5↑ Holy")
r2[4].metric("総収益率",
             f'{stats["total_return_pct"]:+.2f} %',
             f'CAGR {stats["cagr_pct"]:+.2f} %')

st.divider()

# ── タブ ─────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 エクイティカーブ",
    "📅 月別・曜日分析",
    "🎯 トレード分析",
    "📋 トレードログ",
])

# ────────────────────────────────────────────────────────────
# Tab1: エクイティカーブ & 詳細統計
# ────────────────────────────────────────────────────────────
with tab1:
    eq_p  = result["equity_pips"]
    eq_t  = result["equity_times"]
    eq_jpy = [pip_val * p + 1_000_000 for p in eq_p]

    # ドローダウン系列
    peak = eq_p[0]
    dd_series = []
    for ep in eq_p:
        if ep > peak: peak = ep
        ref = 1_000_000 + peak * pip_val
        cur = 1_000_000 + ep  * pip_val
        dd_series.append(-(ref - cur) / ref * 100)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.68, 0.32], vertical_spacing=0.04,
        subplot_titles=("資金推移 (JPY)", "ドローダウン (%)"),
    )
    fig.add_trace(go.Scatter(
        x=eq_t, y=eq_jpy, name="資金",
        mode="lines", line=dict(color="#00c06b", width=2),
        fill="tozeroy", fillcolor="rgba(0,192,107,.08)",
    ), row=1, col=1)
    fig.add_hline(y=1_000_000, row=1, col=1,
                  line=dict(color="#ffd700", dash="dot", width=1),
                  annotation_text="初期資金", annotation_position="top right")
    fig.add_trace(go.Scatter(
        x=eq_t, y=dd_series, name="DD",
        mode="lines", line=dict(color="#ff4b4b", width=1.5),
        fill="tozeroy", fillcolor="rgba(255,75,75,.12)",
    ), row=2, col=1)
    fig.update_layout(
        height=480, showlegend=False, margin=dict(l=10, r=10, t=36, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c0c3cf"),
    )
    fig.update_yaxes(gridcolor="#2d2f3d", zerolinecolor="#2d2f3d")
    fig.update_xaxes(gridcolor="#2d2f3d")
    st.plotly_chart(fig, use_container_width=True)

    # 詳細統計 + 決済理由
    ca, cb = st.columns(2)
    with ca:
        st.markdown("**詳細統計**")
        rows = [
            ("期待値 / トレード", f'{stats["expected_value"]:+.2f} pips'),
            ("平均利益",          f'+{stats["avg_win_pips"]:.1f} pips'),
            ("平均損失",          f'{stats["avg_loss_pips"]:.1f} pips'),
            ("R:R 比",            str(stats["rr_ratio"]) if stats["rr_ratio"] else "∞"),
            ("最大利益 (1回)",    f'+{stats["best_trade_pips"]:.1f} pips'),
            ("最大損失 (1回)",    f'{stats["worst_trade_pips"]:.1f} pips'),
            ("連勝最大",          f'{stats["max_cons_wins"]} 連勝'),
            ("連敗最大",          f'{stats["max_cons_losses"]} 連敗'),
            ("平均保有時間",      f'{stats["avg_holding_h"]:.1f} 時間'),
            ("回復係数",          str(stats["recovery_factor"]) if stats["recovery_factor"] else "∞"),
            ("検証期間",          f'{stats["years"]} 年'),
        ]
        for k, v in rows:
            c1, c2 = st.columns(2)
            c1.caption(k); c2.write(v)

    with cb:
        st.markdown("**決済理由の内訳**")
        total_t = stats["total_trades"]
        for label, cnt in [
            ("🛑 ストップロス",      stats["sl_exits"]),
            ("🎯 テイクプロフィット", stats["tp_exits"]),
            ("📡 シグナル決済",      stats["signal_exits"]),
            ("🔄 リバース（反転）",   stats["reverse_exits"]),
        ]:
            if cnt > 0:
                c1, c2 = st.columns(2)
                c1.caption(label)
                c2.write(f'{cnt} 回 ({cnt/total_t*100:.1f}%)')

        if stats["long_win_rate"] is not None or stats["short_win_rate"] is not None:
            st.markdown("---")
            st.markdown("**方向別勝率**")
            if stats["long_win_rate"] is not None:
                c1, c2 = st.columns(2); c1.caption("LONG"); c2.write(f'{stats["long_win_rate"]} %')
            if stats["short_win_rate"] is not None:
                c1, c2 = st.columns(2); c1.caption("SHORT"); c2.write(f'{stats["short_win_rate"]} %')

# ────────────────────────────────────────────────────────────
# Tab2: 月別・曜日分析
# ────────────────────────────────────────────────────────────
with tab2:
    col_m, col_d = st.columns(2)

    with col_m:
        st.markdown("#### 月別損益 (pips)")
        mp  = stats.get("monthly_pnl", {})
        months = sorted(mp.keys())
        vals   = [mp[m] for m in months]
        fig_m  = go.Figure(go.Bar(
            x=months, y=vals,
            marker_color=["#00c06b" if v >= 0 else "#ff4b4b" for v in vals],
            text=[f"{v:+.0f}" for v in vals], textposition="outside",
        ))
        fig_m.update_layout(
            height=320, showlegend=False, margin=dict(l=0, r=0, t=10, b=60),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c0c3cf"),
            xaxis=dict(tickangle=-45, gridcolor="#2d2f3d"),
            yaxis=dict(gridcolor="#2d2f3d", zerolinecolor="#ffd700"),
        )
        st.plotly_chart(fig_m, use_container_width=True)

    with col_d:
        st.markdown("#### 曜日別損益 (pips)")
        dow = stats.get("dow_pnl", {})
        if dow:
            days = list(dow.keys())
            dpnl = [dow[d]["pnl"] for d in days]
            fig_d = go.Figure(go.Bar(
                x=days, y=dpnl,
                marker_color=["#00c06b" if v >= 0 else "#ff4b4b" for v in dpnl],
                text=[f"{v:+.0f}" for v in dpnl], textposition="outside",
            ))
            fig_d.update_layout(
                height=320, showlegend=False, margin=dict(l=0, r=0, t=10, b=20),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#c0c3cf"),
                xaxis=dict(gridcolor="#2d2f3d"),
                yaxis=dict(gridcolor="#2d2f3d", zerolinecolor="#ffd700"),
            )
            st.plotly_chart(fig_d, use_container_width=True)

    # 月別サマリーテーブル
    if mp:
        st.markdown("#### 月別サマリー")
        mt = stats.get("monthly_trades", {})
        rows_m = []
        for m in sorted(mp.keys()):
            info  = mt.get(m, {})
            tot   = info.get("trades", 0)
            win_n = info.get("wins", 0)
            rows_m.append({
                "月":        m,
                "トレード数": tot,
                "勝":         win_n,
                "負":         tot - win_n,
                "勝率":       f"{win_n/tot*100:.1f}%" if tot > 0 else "–",
                "損益(pips)": f"{mp[m]:+.1f}",
                "損益(JPY)":  f"¥{mp[m]*pip_val:+,.0f}",
            })
        st.dataframe(pd.DataFrame(rows_m), use_container_width=True, hide_index=True)

    # 時間帯別ヒートマップ
    hp = stats.get("hour_pnl", {})
    if hp:
        st.markdown("#### 時間帯別損益 (pips)")
        hours = list(range(24))
        hvals = [hp.get(h, {}).get("pnl", 0) for h in hours]
        fig_h2 = go.Figure(go.Bar(
            x=[f"{h:02d}:00" for h in hours], y=hvals,
            marker_color=["#00c06b" if v >= 0 else "#ff4b4b" for v in hvals],
        ))
        fig_h2.update_layout(
            height=260, showlegend=False, margin=dict(l=0, r=0, t=10, b=20),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c0c3cf"),
            xaxis=dict(title="時 (UTC)", gridcolor="#2d2f3d"),
            yaxis=dict(gridcolor="#2d2f3d", zerolinecolor="#ffd700"),
        )
        st.plotly_chart(fig_h2, use_container_width=True)

# ────────────────────────────────────────────────────────────
# Tab3: トレード分析
# ────────────────────────────────────────────────────────────
with tab3:
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### 損益分布 (pips)")
        fig_hist = go.Figure(go.Histogram(
            x=trades_df["pnl_pips"], nbinsx=30,
            marker_color="#4c9be8",
            marker_line=dict(color="rgba(0,0,0,.3)", width=0.5),
        ))
        fig_hist.add_vline(x=0, line=dict(color="#ffd700", dash="dot"))
        fig_hist.update_layout(
            height=300, showlegend=False, margin=dict(l=0, r=0, t=10, b=20),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c0c3cf"),
            xaxis=dict(title="pips", gridcolor="#2d2f3d"),
            yaxis=dict(title="回数", gridcolor="#2d2f3d"),
        )
        st.plotly_chart(fig_hist, use_container_width=True)

    with col_b:
        st.markdown("#### 勝敗比率")
        fig_pie = go.Figure(go.Pie(
            labels=["勝ち", "負け"],
            values=[stats["win_trades"], stats["loss_trades"]],
            hole=0.55,
            marker=dict(colors=["#00c06b", "#ff4b4b"]),
            textinfo="label+percent",
        ))
        fig_pie.update_layout(
            height=300, showlegend=False, margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c0c3cf"),
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    # 各トレード損益バー
    st.markdown("#### 各トレード損益 (pips)")
    fig_bar = go.Figure(go.Bar(
        x=trades_df["no"], y=trades_df["pnl_pips"], opacity=.85,
        marker_color=["#00c06b" if p > 0 else "#ff4b4b" for p in trades_df["pnl_pips"]],
    ))
    fig_bar.add_hline(y=0, line=dict(color="#808495", width=.8))
    fig_bar.update_layout(
        height=240, showlegend=False, margin=dict(l=0, r=0, t=10, b=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c0c3cf"),
        xaxis=dict(title="トレード#", gridcolor="#2d2f3d"),
        yaxis=dict(gridcolor="#2d2f3d", zerolinecolor="#ffd700"),
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # ロング / ショート 比較表
    cmp_rows = [{"項目": "トレード数", "LONG": stats["long_trades"], "SHORT": stats["short_trades"]}]
    if stats["long_win_rate"]  is not None:
        cmp_rows.append({"項目": "勝率 (%)",  "LONG": stats["long_win_rate"],  "SHORT": stats.get("short_win_rate", "–")})
    lt_df = trades_df[trades_df["direction"] == "LONG"]
    st_df = trades_df[trades_df["direction"] == "SHORT"]
    if len(lt_df) and len(st_df):
        cmp_rows.append({
            "項目":  "平均損益 (pips)",
            "LONG":  f'{lt_df["pnl_pips"].mean():+.1f}',
            "SHORT": f'{st_df["pnl_pips"].mean():+.1f}',
        })
    st.markdown("#### ロング / ショート 比較")
    st.dataframe(pd.DataFrame(cmp_rows), use_container_width=True, hide_index=True)

# ────────────────────────────────────────────────────────────
# Tab4: トレードログ & ダウンロード
# ────────────────────────────────────────────────────────────
with tab4:
    log_df = trades_df[[
        "no", "direction", "entry_time", "exit_time",
        "entry_price", "exit_price", "pnl_pips", "pnl_jpy", "exit_reason",
    ]].copy()
    log_df.columns = [
        "#", "方向", "エントリー日時", "決済日時",
        "エントリー価格", "決済価格", "損益(pips)", "損益(JPY)", "決済理由",
    ]
    st.dataframe(
        log_df, use_container_width=True, hide_index=True,
        column_config={
            "損益(pips)": st.column_config.NumberColumn(format="%.1f"),
            "損益(JPY)":  st.column_config.NumberColumn(format="¥%.0f"),
        },
    )

    # ダウンロードボタン
    csv_buf = io.StringIO()
    trades_df.to_csv(csv_buf, index=False, encoding="utf-8-sig")

    # 統計JSONの辞書値をシリアライズできる形に変換
    stats_export = {
        k: v for k, v in stats.items()
        if not isinstance(v, dict)
    }
    stats_json = json.dumps(
        {"strategy": strategy, "statistics": stats_export},
        ensure_ascii=False, indent=2, default=str,
    )

    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "⬇ トレードログ CSV",
        data=csv_buf.getvalue(),
        file_name=f"backtest_{strategy['symbol']}_{strategy['start_date']}.csv",
        mime="text/csv",
    )
    dl2.download_button(
        "⬇ 統計データ JSON",
        data=stats_json,
        file_name=f"backtest_{strategy['symbol']}_{strategy['start_date']}_stats.json",
        mime="application/json",
    )
