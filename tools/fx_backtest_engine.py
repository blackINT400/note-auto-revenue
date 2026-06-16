"""FX Backtest Engine — データ取得・指標計算・バックテスト実行"""
from __future__ import annotations
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

# ── シンボル変換 ─────────────────────────────────────────────
SYMBOL_YF: dict[str, str] = {
    "USDJPY": "USDJPY=X", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
    "GBPJPY": "GBPJPY=X", "AUDUSD": "AUDUSD=X", "AUDJPY": "AUDJPY=X",
    "NZDUSD": "NZDUSD=X", "USDCHF": "USDCHF=X", "USDCAD": "USDCAD=X",
    "EURJPY": "EURJPY=X", "CHFJPY": "CHFJPY=X", "CADJPY": "CADJPY=X",
    "XAUUSD": "GC=F",     "XAGUSD": "SI=F",
    "BTCUSD": "BTC-USD",  "ETHUSD": "ETH-USD",
}

def get_pip_size(symbol: str) -> float:
    s = symbol.upper()
    if any(x in s for x in ("BTC", "ETH")): return 1.0
    if "XAU" in s or "GC" in s:             return 0.1
    if "XAG" in s or "SI" in s:             return 0.001
    if "JPY" in s:                           return 0.01
    return 0.0001

# yfinanceの最大履歴日数
INTERVAL_MAX_DAYS: dict[str, int] = {
    "1m": 7, "5m": 60, "15m": 60, "30m": 60,
    "1h": 730, "4h": 730, "1d": 9999, "1wk": 9999,
}

# ── Claude 戦略解析 (tool use) ────────────────────────────────
_RULE = {
    "type": "object",
    "required": ["indicator", "condition"],
    "properties": {
        "indicator": {
            "type": "string",
            "enum": ["RSI", "MA", "EMA", "MACD", "BB", "STOCH", "PRICE"],
        },
        "params": {
            "type": "object",
            "description": (
                "RSI:{period:14}, MA/EMAクロス:{fast:5,slow:20}, "
                "MA/EMA単線:{period:20}, MACD:{fast:12,slow:26,signal:9}, "
                "BB:{period:20,std:2.0}, STOCH:{k:14,d:3}"
            ),
        },
        "condition": {
            "type": "string",
            "enum": ["above", "below", "cross_above", "cross_below"],
        },
        "value": {
            "type": "number",
            "description": "比較値 (RSI:30/70, STOCH:20/80等。クロス系は不要)",
        },
    },
}

_TOOL = {
    "name": "set_strategy",
    "description": "自然言語のFX検証内容を構造化パラメータに変換する",
    "input_schema": {
        "type": "object",
        "required": ["symbol", "timeframe", "start_date", "end_date", "entry_long", "description"],
        "properties": {
            "symbol":             {"type": "string"},
            "timeframe":          {"type": "string", "enum": ["1m","5m","15m","30m","1h","4h","1d","1wk"]},
            "start_date":         {"type": "string", "description": "YYYY-MM-DD"},
            "end_date":           {"type": "string", "description": "YYYY-MM-DD"},
            "entry_long":         {"type": "array", "items": _RULE, "description": "ロングエントリー条件(AND)"},
            "entry_short":        {"type": "array", "items": _RULE, "description": "ショートエントリー条件(AND)"},
            "exit_long":          {"type": "array", "items": _RULE, "description": "ロング決済条件(AND)"},
            "exit_short":         {"type": "array", "items": _RULE, "description": "ショート決済条件(AND)"},
            "stop_loss_pips":     {"type": "number"},
            "take_profit_pips":   {"type": "number"},
            "pip_value":          {"type": "number", "description": "1pipあたり損益(JPY)。デフォルト1000"},
            "description":        {"type": "string"},
        },
    },
}


def parse_strategy(description: str, client: Anthropic) -> dict:
    """Claude tool use で自然言語 → 戦略JSON"""
    today      = datetime.today().strftime("%Y-%m-%d")
    three_ago  = (datetime.today() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
    system = (
        f"あなたはFX戦略アナリストです。今日={today}。\n"
        f"日付未指定→ start={three_ago}, end={today}。\n"
        "通貨未指定→USDJPY。時間足未指定→1h。pip_value未指定→1000。\n"
        "entry_longとentry_shortの両方を埋める（ロング・ショート両方を検証する）。\n"
        "ロングのみの戦略ならentry_shortは空配列[]にする。"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=system,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "set_strategy"},
        messages=[{"role": "user", "content": description}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            p = block.input
            p.setdefault("entry_short",      [])
            p.setdefault("exit_long",         [])
            p.setdefault("exit_short",        [])
            p.setdefault("stop_loss_pips",    None)
            p.setdefault("take_profit_pips",  None)
            p.setdefault("pip_value",         1000)
            return p
    raise ValueError("Claude API から戦略パラメータを取得できませんでした")


# ── データ取得 ────────────────────────────────────────────────

def fetch_data(symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
    """yfinance で OHLCV 取得。4h は 1h → リサンプル"""
    yf_sym = SYMBOL_YF.get(symbol.upper(), symbol.upper() + "=X")
    start  = datetime.strptime(start_date, "%Y-%m-%d")
    end    = datetime.strptime(end_date,   "%Y-%m-%d")
    days   = (end - start).days

    fetch_tf   = "1h" if timeframe == "4h" else timeframe
    resample4h = timeframe == "4h"
    max_days   = INTERVAL_MAX_DAYS.get(fetch_tf, 9999)
    warning    = None

    if days > max_days:
        fetch_tf   = "1d"
        resample4h = False
        warning    = f"⚠️ {timeframe}足の最大取得期間({max_days}日)を超えるため、日足データで代替します"

    ticker = yf.Ticker(yf_sym)
    df = ticker.history(start=start_date, end=end_date, interval=fetch_tf, auto_adjust=True)

    if df.empty:
        raise ValueError(f"データ取得失敗: {yf_sym} ({fetch_tf})")

    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df = df.dropna()

    if resample4h:
        df = df.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()

    df.attrs["warning"] = warning
    df.attrs["bars"]    = len(df)
    return df


# ── インジケーター計算 ────────────────────────────────────────

def _rsi(close: pd.Series, period: int) -> pd.Series:
    d    = close.diff()
    gain = d.clip(lower=0).ewm(com=period - 1, adjust=True).mean()
    loss = (-d.clip(upper=0)).ewm(com=period - 1, adjust=True).mean()
    rs   = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_indicators(df: pd.DataFrame, rules: list[dict]) -> pd.DataFrame:
    r = df.copy()
    for rule in rules:
        ind = (rule.get("indicator") or "").upper()
        p   = rule.get("params") or {}

        if ind == "RSI":
            per = p.get("period", 14)
            col = f"RSI_{per}"
            if col not in r.columns:
                r[col] = _rsi(r["close"], per)

        elif ind in ("MA", "SMA"):
            if "fast" in p and "slow" in p:
                f, s = p["fast"], p["slow"]
                if f"MA_fast_{f}" not in r.columns:
                    r[f"MA_fast_{f}"] = r["close"].rolling(f).mean()
                if f"MA_slow_{s}" not in r.columns:
                    r[f"MA_slow_{s}"] = r["close"].rolling(s).mean()
            else:
                per = p.get("period", 20)
                if f"MA_{per}" not in r.columns:
                    r[f"MA_{per}"] = r["close"].rolling(per).mean()

        elif ind == "EMA":
            if "fast" in p and "slow" in p:
                f, s = p["fast"], p["slow"]
                if f"EMA_fast_{f}" not in r.columns:
                    r[f"EMA_fast_{f}"] = r["close"].ewm(span=f, adjust=False).mean()
                if f"EMA_slow_{s}" not in r.columns:
                    r[f"EMA_slow_{s}"] = r["close"].ewm(span=s, adjust=False).mean()
            else:
                per = p.get("period", 20)
                if f"EMA_{per}" not in r.columns:
                    r[f"EMA_{per}"] = r["close"].ewm(span=per, adjust=False).mean()

        elif ind == "MACD":
            if "MACD_line" not in r.columns:
                f   = p.get("fast", 12)
                s   = p.get("slow", 26)
                sig = p.get("signal", 9)
                ef  = r["close"].ewm(span=f, adjust=False).mean()
                es  = r["close"].ewm(span=s, adjust=False).mean()
                r["MACD_line"]   = ef - es
                r["MACD_signal"] = r["MACD_line"].ewm(span=sig, adjust=False).mean()
                r["MACD_hist"]   = r["MACD_line"] - r["MACD_signal"]

        elif ind in ("BB", "BOLLINGER"):
            per = p.get("period", 20)
            std = p.get("std", 2.0)
            if f"BB_upper_{per}" not in r.columns:
                ma   = r["close"].rolling(per).mean()
                sd   = r["close"].rolling(per).std()
                r[f"BB_upper_{per}"] = ma + std * sd
                r[f"BB_mid_{per}"]   = ma
                r[f"BB_lower_{per}"] = ma - std * sd

        elif ind == "STOCH":
            kp = p.get("k", 14)
            dp = p.get("d", 3)
            if "STOCH_K" not in r.columns:
                lo = r["low"].rolling(kp).min()
                hi = r["high"].rolling(kp).max()
                r["STOCH_K"] = 100 * (r["close"] - lo) / (hi - lo).replace(0, np.nan)
                r["STOCH_D"] = r["STOCH_K"].rolling(dp).mean()
    return r


def _check(df: pd.DataFrame, rule: dict, idx, prev_idx=None) -> bool:
    ind  = (rule.get("indicator") or "").upper()
    p    = rule.get("params") or {}
    cond = rule.get("condition", "")
    val  = rule.get("value")

    try:
        if ind == "RSI":
            per = p.get("period", 14)
            v   = df.at[idx, f"RSI_{per}"]
            if pd.isna(v): return False
            if cond == "below":  return v < float(val)
            if cond == "above":  return v > float(val)

        elif ind in ("MA", "SMA"):
            if "fast" in p and "slow" in p:
                fv = df.at[idx, f"MA_fast_{p['fast']}"]
                sv = df.at[idx, f"MA_slow_{p['slow']}"]
                if pd.isna(fv) or pd.isna(sv): return False
                if cond == "above": return fv > sv
                if cond == "below": return fv < sv
                if prev_idx is not None:
                    pf = df.at[prev_idx, f"MA_fast_{p['fast']}"]
                    ps = df.at[prev_idx, f"MA_slow_{p['slow']}"]
                    if cond == "cross_above": return pf <= ps and fv > sv
                    if cond == "cross_below": return pf >= ps and fv < sv
            else:
                per = p.get("period", 20)
                mv  = df.at[idx, f"MA_{per}"]
                cv  = df.at[idx, "close"]
                if pd.isna(mv): return False
                if cond == "above": return cv > mv
                if cond == "below": return cv < mv

        elif ind == "EMA":
            if "fast" in p and "slow" in p:
                fv = df.at[idx, f"EMA_fast_{p['fast']}"]
                sv = df.at[idx, f"EMA_slow_{p['slow']}"]
                if pd.isna(fv) or pd.isna(sv): return False
                if cond == "above": return fv > sv
                if cond == "below": return fv < sv
                if prev_idx is not None:
                    pf = df.at[prev_idx, f"EMA_fast_{p['fast']}"]
                    ps = df.at[prev_idx, f"EMA_slow_{p['slow']}"]
                    if cond == "cross_above": return pf <= ps and fv > sv
                    if cond == "cross_below": return pf >= ps and fv < sv
            else:
                per = p.get("period", 20)
                ev  = df.at[idx, f"EMA_{per}"]
                cv  = df.at[idx, "close"]
                if pd.isna(ev): return False
                if cond == "above": return cv > ev
                if cond == "below": return cv < ev

        elif ind == "MACD":
            h = df.at[idx, "MACD_hist"]
            if pd.isna(h): return False
            if cond == "above": return h > 0
            if cond == "below": return h < 0
            if prev_idx is not None:
                ph = df.at[prev_idx, "MACD_hist"]
                if cond == "cross_above": return ph <= 0 and h > 0
                if cond == "cross_below": return ph >= 0 and h < 0

        elif ind in ("BB", "BOLLINGER"):
            per = p.get("period", 20)
            cv  = df.at[idx, "close"]
            if cond == "above":  return cv > df.at[idx, f"BB_upper_{per}"]
            if cond == "below":  return cv < df.at[idx, f"BB_lower_{per}"]
            if prev_idx is not None:
                pc = df.at[prev_idx, "close"]
                if cond == "cross_above": return pc <= df.at[prev_idx, f"BB_upper_{per}"] and cv > df.at[idx, f"BB_upper_{per}"]
                if cond == "cross_below": return pc >= df.at[prev_idx, f"BB_lower_{per}"] and cv < df.at[idx, f"BB_lower_{per}"]

        elif ind == "STOCH":
            k = df.at[idx, "STOCH_K"]
            if pd.isna(k): return False
            if cond == "above": return k > float(val)
            if cond == "below": return k < float(val)
            if prev_idx is not None:
                pk = df.at[prev_idx, "STOCH_K"]
                if cond == "cross_above": return pk <= float(val) and k > float(val)
                if cond == "cross_below": return pk >= float(val) and k < float(val)

        elif ind == "PRICE":
            cv = df.at[idx, "close"]
            if cond == "above": return cv > float(val)
            if cond == "below": return cv < float(val)

    except (KeyError, TypeError, ValueError):
        pass
    return False


def _all(df, rules, idx, prev_idx) -> bool:
    return bool(rules) and all(_check(df, r, idx, prev_idx) for r in rules)


# ── バックテスト実行 ──────────────────────────────────────────

def run_backtest(df: pd.DataFrame, strategy: dict) -> dict:
    pip_size = get_pip_size(strategy["symbol"])
    pip_val  = float(strategy.get("pip_value") or 1000)
    sl_pips  = strategy.get("stop_loss_pips")
    tp_pips  = strategy.get("take_profit_pips")

    el = strategy.get("entry_long",  []) or []
    es = strategy.get("entry_short", []) or []
    xl = strategy.get("exit_long",   []) or []
    xs = strategy.get("exit_short",  []) or []

    df = add_indicators(df, el + es + xl + xs).dropna()
    idx_list = list(df.index)
    if len(idx_list) < 2:
        raise ValueError("インジケーター計算後のデータが不足しています")

    pnl_cum  = 0.0
    position = None
    entry_px = 0.0
    entry_idx = None
    trades   = []
    eq_pips  = [0.0]
    eq_times = [str(idx_list[0])]

    def _close(direction, exit_px, reason, idx):
        nonlocal pnl_cum, position, entry_px, entry_idx
        if direction == "long":
            pnl_p = (exit_px - entry_px) / pip_size
        else:
            pnl_p = (entry_px - exit_px) / pip_size
        pnl_cum += pnl_p
        trades.append({
            "no":          len(trades) + 1,
            "direction":   "LONG" if direction == "long" else "SHORT",
            "entry_time":  str(entry_idx),
            "exit_time":   str(idx),
            "entry_price": round(entry_px, 5),
            "exit_price":  round(exit_px, 5),
            "pnl_pips":    round(pnl_p, 1),
            "pnl_jpy":     round(pnl_p * pip_val, 0),
            "exit_reason": reason,
            "equity_pips": round(pnl_cum, 1),
        })
        eq_pips.append(pnl_cum)
        eq_times.append(str(idx))
        position = None

    for i in range(1, len(idx_list)):
        idx  = idx_list[i]
        pidx = idx_list[i - 1]
        row  = df.loc[idx]

        if position is None:
            if el and _all(df, el, idx, pidx):
                position  = "long"
                entry_px  = row["close"]
                entry_idx = idx
            elif es and _all(df, es, idx, pidx):
                position  = "short"
                entry_px  = row["close"]
                entry_idx = idx

        elif position == "long":
            ex_px = ex_r = None
            if sl_pips and row["low"] <= entry_px - sl_pips * pip_size:
                ex_px, ex_r = entry_px - sl_pips * pip_size, "SL"
            elif tp_pips and row["high"] >= entry_px + tp_pips * pip_size:
                ex_px, ex_r = entry_px + tp_pips * pip_size, "TP"
            elif xl and _all(df, xl, idx, pidx):
                ex_px, ex_r = row["close"], "Signal"
            elif es and _all(df, es, idx, pidx):
                ex_px, ex_r = row["close"], "Reverse"

            if ex_px is not None:
                _close("long", ex_px, ex_r, idx)
                if ex_r == "Reverse":
                    position = "short"; entry_px = row["close"]; entry_idx = idx

        elif position == "short":
            ex_px = ex_r = None
            if sl_pips and row["high"] >= entry_px + sl_pips * pip_size:
                ex_px, ex_r = entry_px + sl_pips * pip_size, "SL"
            elif tp_pips and row["low"] <= entry_px - tp_pips * pip_size:
                ex_px, ex_r = entry_px - tp_pips * pip_size, "TP"
            elif xs and _all(df, xs, idx, pidx):
                ex_px, ex_r = row["close"], "Signal"
            elif el and _all(df, el, idx, pidx):
                ex_px, ex_r = row["close"], "Reverse"

            if ex_px is not None:
                _close("short", ex_px, ex_r, idx)
                if ex_r == "Reverse":
                    position = "long"; entry_px = row["close"]; entry_idx = idx

    return {"trades": trades, "equity_pips": eq_pips, "equity_times": eq_times, "pip_value": pip_val}


# ── 統計計算 ─────────────────────────────────────────────────

def calculate_statistics(result: dict, strategy: dict) -> dict:
    trades  = result["trades"]
    pip_val = result["pip_value"]
    eq      = result["equity_pips"]

    if not trades:
        return {"total_trades": 0}

    dt   = pd.DataFrame(trades)
    pnl  = dt["pnl_pips"]
    wins = dt[pnl > 0]
    loss = dt[pnl <= 0]
    longs  = dt[dt["direction"] == "LONG"]
    shorts = dt[dt["direction"] == "SHORT"]

    gross_win  = wins["pnl_pips"].sum()  if len(wins)  > 0 else 0.0
    gross_loss = loss["pnl_pips"].sum()  if len(loss)  > 0 else 0.0  # ≤0
    net_pips   = pnl.sum()
    pf         = (gross_win / abs(gross_loss)) if gross_loss != 0 else None
    avg_win    = wins["pnl_pips"].mean() if len(wins)  > 0 else 0.0
    avg_loss   = loss["pnl_pips"].mean() if len(loss)  > 0 else 0.0
    rr         = (avg_win / abs(avg_loss)) if avg_loss != 0 else None
    ev         = pnl.mean()

    long_wr  = (longs["pnl_pips"]  > 0).mean() * 100 if len(longs)  > 0 else None
    short_wr = (shorts["pnl_pips"] > 0).mean() * 100 if len(shorts) > 0 else None

    # Max drawdown (from equity curve)
    peak = eq[0]; max_dd = 0.0
    for ep in eq:
        if ep > peak: peak = ep
        max_dd = max(max_dd, peak - ep)

    # SQN = sqrt(N) * mean(R) / std(R)
    sqn = (ev / pnl.std() * np.sqrt(len(pnl))) if pnl.std() > 0 and len(pnl) >= 5 else 0.0

    # Annualized metrics from trade-level returns
    eq_arr = np.array(eq)
    rets   = np.diff(eq_arr)  # per-trade pip returns

    start_dt = datetime.strptime(strategy["start_date"], "%Y-%m-%d")
    end_dt   = datetime.strptime(strategy["end_date"],   "%Y-%m-%d")
    years    = max((end_dt - start_dt).days / 365.25, 0.01)
    tpy      = len(trades) / years  # trades per year

    if len(rets) > 1 and rets.std() > 0:
        sharpe  = (rets.mean() / rets.std()) * np.sqrt(tpy)
        neg     = rets[rets < 0]
        dstd    = neg.std() if len(neg) > 1 else (abs(neg[0]) if len(neg) == 1 else 0)
        sortino = (rets.mean() / dstd * np.sqrt(tpy)) if dstd > 0 else 0.0
    else:
        sharpe = sortino = 0.0

    init_cap   = 1_000_000.0
    final_cap  = init_cap + net_pips * pip_val
    total_ret  = (final_cap / init_cap - 1) * 100
    cagr       = ((final_cap / init_cap) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    max_dd_pct = (max_dd * pip_val / init_cap * 100)
    calmar     = (cagr / max_dd_pct) if max_dd_pct > 0 else 0.0
    recovery   = (net_pips / max_dd) if max_dd > 0 else None

    # 連勝・連敗
    max_cw = max_cl = cw = cl = 0
    for p in pnl:
        if p > 0: cw += 1; cl = 0; max_cw = max(max_cw, cw)
        else:     cl += 1; cw = 0; max_cl = max(max_cl, cl)

    # 平均保有時間 (hours)
    def hold_h(row):
        try: return (pd.to_datetime(row["exit_time"]) - pd.to_datetime(row["entry_time"])).total_seconds() / 3600
        except: return 0
    avg_hold_h = dt.apply(hold_h, axis=1).mean()

    # 決済理由
    exits = dt["exit_reason"].value_counts().to_dict()

    # 月別損益
    dt["month"] = pd.to_datetime(dt["exit_time"]).dt.to_period("M").astype(str)
    monthly_pnl = dt.groupby("month")["pnl_pips"].sum().to_dict()
    monthly_trades = dt.groupby("month").apply(
        lambda x: {"trades": len(x), "wins": int((x["pnl_pips"] > 0).sum())}
    ).to_dict()

    # 曜日別
    DOW = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"}
    dt["dow"] = pd.to_datetime(dt["exit_time"]).dt.dayofweek
    dow_pnl: dict = {}
    for d, name in DOW.items():
        sub = dt[dt["dow"] == d]
        if len(sub) > 0:
            dow_pnl[name] = {
                "trades": len(sub),
                "wins":   int((sub["pnl_pips"] > 0).sum()),
                "pnl":    round(float(sub["pnl_pips"].sum()), 1),
            }

    # 時間帯別
    dt["hour"] = pd.to_datetime(dt["entry_time"]).dt.hour
    hour_pnl: dict = {
        int(h): {"pnl": round(float(g["pnl_pips"].sum()), 1), "trades": len(g)}
        for h, g in dt.groupby("hour")
    }

    return {
        "total_trades":        len(dt),
        "long_trades":         len(longs),
        "short_trades":        len(shorts),
        "win_trades":          len(wins),
        "loss_trades":         len(loss),
        "win_rate":            round(float((pnl > 0).mean()) * 100, 1),
        "long_win_rate":       round(long_wr,  1) if long_wr  is not None else None,
        "short_win_rate":      round(short_wr, 1) if short_wr is not None else None,
        "net_pips":            round(net_pips,  1),
        "gross_profit_pips":   round(gross_win, 1),
        "gross_loss_pips":     round(gross_loss,1),
        "profit_factor":       round(pf, 2)  if pf  is not None else None,
        "expected_value":      round(ev, 2),
        "avg_win_pips":        round(avg_win,  1),
        "avg_loss_pips":       round(avg_loss, 1),
        "rr_ratio":            round(rr, 2)  if rr  is not None else None,
        "best_trade_pips":     round(float(pnl.max()), 1),
        "worst_trade_pips":    round(float(pnl.min()), 1),
        "max_drawdown_pips":   round(max_dd,     1),
        "max_drawdown_pct":    round(max_dd_pct, 2),
        "sharpe_ratio":        round(sharpe,  2),
        "sortino_ratio":       round(sortino, 2),
        "calmar_ratio":        round(calmar,  2),
        "recovery_factor":     round(recovery, 2) if recovery is not None else None,
        "sqn":                 round(sqn, 2),
        "max_cons_wins":       max_cw,
        "max_cons_losses":     max_cl,
        "avg_holding_h":       round(avg_hold_h, 1),
        "initial_capital":     init_cap,
        "final_capital":       round(final_cap, 0),
        "total_return_pct":    round(total_ret, 2),
        "cagr_pct":            round(cagr, 2),
        "net_pnl_jpy":         round(net_pips * pip_val, 0),
        "sl_exits":            exits.get("SL",      0),
        "tp_exits":            exits.get("TP",      0),
        "signal_exits":        exits.get("Signal",  0),
        "reverse_exits":       exits.get("Reverse", 0),
        "years":               round(years, 2),
        "pip_value":           pip_val,
        "monthly_pnl":         monthly_pnl,
        "monthly_trades":      monthly_trades,
        "dow_pnl":             dow_pnl,
        "hour_pnl":            hour_pnl,
    }
