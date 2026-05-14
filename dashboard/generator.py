"""
dashboard/generator.py: KPI JSONを読み込み、単一HTMLダッシュボードを生成する

呼び出し元:
  - empire_main.py --mode monthly（毎月1日）
  - empire_main.py --mode daily（日次更新）
  - 手動: python dashboard/generator.py

出力: dashboard/output/dashboard.html
"""
import json
import logging
import sys
from datetime import date
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
DATA_DIR = DASHBOARD_DIR / "data"
OUTPUT_DIR = DASHBOARD_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REPO_OWNER = "blackINT400"
REPO_NAME = "note-auto-revenue"
PAGES_URL = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/"


# ── データ読み込み ─────────────────────────────────────────────────────────────

def _load_latest_kpi() -> dict:
    """今月の最新スナップショットを返す。なければ空データ。"""
    today = date.today()
    year_month = today.strftime("%Y-%m")
    path = DATA_DIR / f"kpi_{year_month}.json"

    if not path.exists():
        # データがなければサンプルを生成して返す
        logger.warning("[generator] KPIデータなし。サンプルデータで生成します。")
        return _sample_kpi()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _sample_kpi()

    snapshots = data.get("snapshots", [])
    if not snapshots:
        return _sample_kpi()

    latest = snapshots[-1]
    latest["history"] = data.get("history", [])
    latest["all_snapshots"] = snapshots
    return latest


def _sample_kpi() -> dict:
    """データが存在しない場合のサンプルKPI（ゼロ値）"""
    today = str(date.today())
    return {
        "date": today,
        "total": {
            "revenue": 0,
            "cost": 0,
            "profit": 0,
            "roi": 0,
            "cost_limit": 5000,
            "cost_usage_pct": 0,
            "monthly_target": 30000,
            "available_budget": 0,
        },
        "businesses": [],
        "history": [],
        "all_snapshots": [],
    }


def _prev_month_total(history: list) -> dict:
    """直前月のtotalを history から取得"""
    if len(history) >= 2:
        return history[-2]
    return {"revenue": 0, "cost": 0, "profit": 0}


def _pct_change(current: float, previous: float) -> str:
    if previous == 0:
        return "+0.0%" if current == 0 else "+∞%"
    pct = (current - previous) / previous * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _roi_color(roi: float) -> str:
    if roi >= 100:
        return "#22c55e"   # green
    elif roi >= 0:
        return "#f59e0b"   # amber
    else:
        return "#ef4444"   # red


def _status_badge(status: str) -> str:
    colors = {
        "active": ("#dcfce7", "#166534"),
        "paused": ("#fef9c3", "#854d0e"),
        "killed": ("#fee2e2", "#991b1b"),
        "pending_human_approval": ("#dbeafe", "#1e40af"),
    }
    bg, text = colors.get(status, ("#f3f4f6", "#374151"))
    labels = {
        "active": "稼働中",
        "paused": "一時停止",
        "killed": "停止",
        "pending_human_approval": "承認待ち",
    }
    label = labels.get(status, status)
    return (
        f'<span style="background:{bg};color:{text};padding:2px 8px;'
        f'border-radius:9999px;font-size:0.75rem;font-weight:600;">'
        f'{label}</span>'
    )


# ── HTML生成 ──────────────────────────────────────────────────────────────────

def generate(kpi: dict | None = None) -> Path:
    """HTMLダッシュボードを生成して output/dashboard.html に保存する"""
    if kpi is None:
        kpi = _load_latest_kpi()

    today_str = kpi.get("date", str(date.today()))
    total = kpi.get("total", {})
    businesses = kpi.get("businesses", [])
    history = kpi.get("history", [])
    all_snapshots = kpi.get("all_snapshots", [])

    rev = float(total.get("revenue", 0))
    cost = float(total.get("cost", 0))
    profit = float(total.get("profit", 0))
    roi = float(total.get("roi", 0))
    cost_limit = float(total.get("cost_limit", 5000))
    cost_pct = float(total.get("cost_usage_pct", 0))
    monthly_target = float(total.get("monthly_target", 30000))

    prev = _prev_month_total(history)
    rev_change = _pct_change(rev, float(prev.get("revenue", 0)))
    profit_change = _pct_change(profit, float(prev.get("profit", 0)))

    active_count = sum(1 for b in businesses if b.get("status") == "active")
    target_remain = max(0.0, monthly_target - rev)
    target_pct = min(100.0, rev / monthly_target * 100) if monthly_target > 0 else 0.0

    # ── 月別グラフ用データ ───────────────────────────────────────────────────
    hist_labels = json.dumps([h["year_month"] for h in history], ensure_ascii=False)
    hist_revenues = json.dumps([h.get("revenue", 0) for h in history])
    hist_costs = json.dumps([h.get("cost", 0) for h in history])
    hist_profits = json.dumps([h.get("profit", 0) for h in history])

    # ── 日次推移（今月のスナップショット）─────────────────────────────────────
    daily_labels = json.dumps([s["date"] for s in all_snapshots], ensure_ascii=False)
    daily_revenues = json.dumps([s.get("total", {}).get("revenue", 0) for s in all_snapshots])
    daily_profits = json.dumps([s.get("total", {}).get("profit", 0) for s in all_snapshots])

    # ── 累積純利益（全月の profit を累積）────────────────────────────────────
    cumulative = []
    running = 0.0
    for h in history:
        running += float(h.get("profit", 0))
        cumulative.append(round(running, 0))
    cumulative_json = json.dumps(cumulative)

    # ── 事業別棒グラフ ────────────────────────────────────────────────────────
    biz_labels = json.dumps([b["id"] for b in businesses], ensure_ascii=False)
    biz_revenues = json.dumps([b.get("revenue", 0) for b in businesses])
    biz_rois = json.dumps([b.get("roi", 0) for b in businesses])

    # ── アラート ───────────────────────────────────────────────────────────────
    neg_roi_biases = [b for b in businesses if float(b.get("roi", 0)) < 0]
    alert_rows = ""
    for b in neg_roi_biases:
        alert_rows += (
            f'<tr style="background:#fef2f2;">'
            f'<td style="padding:8px 12px;border-bottom:1px solid #fecaca;">{b["id"]}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #fecaca;color:#ef4444;font-weight:600;">'
            f'ROI {b.get("roi",0):.1f}%</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #fecaca;">'
            f'収益:{b.get("revenue",0):,.0f}円 / コスト:{b.get("cost",0):,.0f}円</td>'
            f'</tr>'
        )
    alert_section = (
        f'<div class="card alert-card">'
        f'<h3>⚠️ ROIマイナス事業</h3>'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr style="background:#fee2e2;">'
        f'<th style="padding:8px 12px;text-align:left;">事業ID</th>'
        f'<th style="padding:8px 12px;text-align:left;">ROI</th>'
        f'<th style="padding:8px 12px;text-align:left;">詳細</th>'
        f'</tr></thead><tbody>{alert_rows}</tbody></table></div>'
    ) if neg_roi_biases else ""

    # ── 事業テーブル ──────────────────────────────────────────────────────────
    biz_table_rows = ""
    for b in businesses:
        roi_v = float(b.get("roi", 0))
        roi_col = _roi_color(roi_v)
        badge = _status_badge(b.get("status", "active"))
        rev_v = float(b.get("revenue", 0))
        max_rev = max((float(x.get("revenue", 0)) for x in businesses), default=1) or 1
        bar_pct = min(100, rev_v / max_rev * 100)
        biz_table_rows += f"""
        <tr class="biz-row">
          <td style="padding:10px 12px;font-weight:600;">{b["id"]}</td>
          <td style="padding:10px 12px;">{badge}</td>
          <td style="padding:10px 12px;">
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="flex:1;background:#e5e7eb;border-radius:4px;height:8px;">
                <div style="width:{bar_pct:.1f}%;background:#6366f1;border-radius:4px;height:8px;"></div>
              </div>
              <span style="min-width:80px;text-align:right;">¥{rev_v:,.0f}</span>
            </div>
          </td>
          <td style="padding:10px 12px;">¥{float(b.get("cost",0)):,.0f}</td>
          <td style="padding:10px 12px;font-weight:600;color:{roi_col};">{roi_v:.1f}%</td>
        </tr>"""

    # ── HTML本体 ──────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>収益ダッシュボード — 自律収益帝国</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #f8fafc;
    --card: #ffffff;
    --border: #e2e8f0;
    --text: #1e293b;
    --muted: #64748b;
    --accent: #6366f1;
    --green: #22c55e;
    --red: #ef4444;
    --amber: #f59e0b;
    --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.06);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --shadow: 0 1px 3px rgba(0,0,0,.4);
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, "Hiragino Sans", "Meiryo", sans-serif;
         background: var(--bg); color: var(--text); padding: 16px; }}
  h1 {{ font-size: 1.4rem; font-weight: 700; margin-bottom: 4px; }}
  h2 {{ font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; color: var(--text); }}
  h3 {{ font-size: 0.95rem; font-weight: 600; margin-bottom: 10px; }}
  .header {{ display:flex; justify-content:space-between; align-items:center;
             margin-bottom:20px; flex-wrap:wrap; gap:8px; }}
  .subtitle {{ color: var(--muted); font-size: 0.85rem; }}
  .updated {{ color: var(--muted); font-size: 0.8rem; }}
  .grid-4 {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:20px; }}
  .grid-2 {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin-bottom:20px; }}
  @media (max-width:900px) {{ .grid-4 {{ grid-template-columns:repeat(2,1fr); }} }}
  @media (max-width:500px) {{ .grid-4, .grid-2 {{ grid-template-columns:1fr; }} }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px;
           padding:16px; box-shadow:var(--shadow); }}
  .card.full {{ grid-column:1/-1; }}
  .kpi-label {{ font-size:0.78rem; color:var(--muted); margin-bottom:4px; }}
  .kpi-value {{ font-size:1.6rem; font-weight:700; }}
  .kpi-change {{ font-size:0.8rem; margin-top:4px; }}
  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}
  .alert-card {{ border-color:#fca5a5; }}
  .section-title {{ font-size:1.05rem; font-weight:600; margin:20px 0 12px;
                    padding-bottom:6px; border-bottom:2px solid var(--border); }}
  .tabs {{ display:flex; gap:6px; margin-bottom:12px; }}
  .tab {{ padding:5px 12px; border-radius:20px; font-size:0.82rem; cursor:pointer;
          border:1px solid var(--border); background:var(--bg); color:var(--muted);
          transition:all .2s; }}
  .tab.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .progress-wrap {{ margin-bottom:10px; }}
  .progress-label {{ display:flex; justify-content:space-between; font-size:0.82rem;
                     color:var(--muted); margin-bottom:4px; }}
  .progress-bar {{ height:10px; border-radius:5px; background:#e2e8f0; overflow:hidden; }}
  .progress-fill {{ height:100%; border-radius:5px; transition:width .4s; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.88rem; }}
  th {{ padding:10px 12px; text-align:left; font-weight:600; font-size:0.8rem;
        color:var(--muted); border-bottom:2px solid var(--border); }}
  .biz-row:hover {{ background: rgba(99,102,241,.05); }}
  canvas {{ max-height:280px; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>📊 自律収益帝国 — 月次ダッシュボード</h1>
    <div class="subtitle">{today_str[:7]} · {active_count}事業稼働中</div>
  </div>
  <div class="updated">最終更新: {today_str}</div>
</div>

<!-- KPIカード -->
<div class="grid-4">
  <div class="card">
    <div class="kpi-label">今月の収益</div>
    <div class="kpi-value">¥{rev:,.0f}</div>
    <div class="kpi-change {'positive' if '+' in rev_change else 'negative'}">先月比 {rev_change}</div>
  </div>
  <div class="card">
    <div class="kpi-label">今月のコスト</div>
    <div class="kpi-value">¥{cost:,.0f}</div>
    <div class="kpi-change" style="color:var(--muted);">上限 ¥{cost_limit:,.0f}</div>
  </div>
  <div class="card">
    <div class="kpi-label">今月の純利益</div>
    <div class="kpi-value" style="color:{'var(--green)' if profit >= 0 else 'var(--red)'};">¥{profit:,.0f}</div>
    <div class="kpi-change {'positive' if profit >= 0 else 'negative'}">ROI {roi:.1f}% （先月比 {profit_change}）</div>
  </div>
  <div class="card">
    <div class="kpi-label">稼働中の事業数</div>
    <div class="kpi-value">{active_count}</div>
    <div class="kpi-change" style="color:var(--muted);">全{len(businesses)}事業中</div>
  </div>
</div>

<!-- アラート -->
{alert_section}

<!-- 進捗バー -->
<div class="card" style="margin-bottom:20px;">
  <h3>📈 今月の進捗</h3>
  <div class="progress-wrap">
    <div class="progress-label">
      <span>月間目標: ¥{monthly_target:,.0f}</span>
      <span>¥{rev:,.0f} / あと ¥{target_remain:,.0f}</span>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{target_pct:.1f}%;background:var(--green);"></div>
    </div>
  </div>
  <div class="progress-wrap">
    <div class="progress-label">
      <span>月間コスト使用率</span>
      <span>{cost_pct:.1f}% (¥{cost:,.0f} / ¥{cost_limit:,.0f})</span>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{min(cost_pct,100):.1f}%;background:{'var(--red)' if cost_pct > 80 else 'var(--amber)'};"></div>
    </div>
  </div>
</div>

<!-- 月別グラフ -->
<div class="section-title">📅 月別推移</div>
<div class="card" style="margin-bottom:20px;">
  <div class="tabs">
    <button class="tab active" onclick="setRange(3,this)">3ヶ月</button>
    <button class="tab" onclick="setRange(6,this)">6ヶ月</button>
    <button class="tab" onclick="setRange(12,this)">12ヶ月</button>
  </div>
  <canvas id="monthlyChart"></canvas>
</div>

<!-- 事業別パフォーマンス -->
<div class="section-title">🏢 事業別パフォーマンス</div>
<div class="grid-2" style="margin-bottom:20px;">
  <div class="card">
    <h3>収益ランキング</h3>
    <canvas id="bizChart"></canvas>
  </div>
  <div class="card">
    <h3>ROI比較</h3>
    <canvas id="roiChart"></canvas>
  </div>
</div>

<!-- 事業テーブル -->
<div class="card" style="margin-bottom:20px;">
  <h3>事業別詳細</h3>
  <div style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>事業ID</th>
          <th>ステータス</th>
          <th>収益</th>
          <th>コスト</th>
          <th>ROI</th>
        </tr>
      </thead>
      <tbody>
        {biz_table_rows if biz_table_rows else '<tr><td colspan="5" style="padding:16px;text-align:center;color:var(--muted);">事業データなし</td></tr>'}
      </tbody>
    </table>
  </div>
</div>

<!-- 累積純利益グラフ -->
<div class="section-title">📈 累積純利益</div>
<div class="card" style="margin-bottom:20px;">
  <canvas id="cumulativeChart"></canvas>
</div>

<!-- 今月日次推移 -->
<div class="section-title">📆 今月の日次推移</div>
<div class="card" style="margin-bottom:20px;">
  <canvas id="dailyChart"></canvas>
</div>

<div style="text-align:center;color:var(--muted);font-size:0.78rem;margin-top:24px;padding-bottom:12px;">
  自動生成 by note-auto-revenue · {today_str} ·
  <a href="{PAGES_URL}" style="color:var(--accent);">{PAGES_URL}</a>
</div>

<script>
const histLabels = {hist_labels};
const histRevenues = {hist_revenues};
const histCosts = {hist_costs};
const histProfits = {hist_profits};
const cumulative = {cumulative_json};
const bizLabels = {biz_labels};
const bizRevenues = {biz_revenues};
const bizRois = {biz_rois};
const dailyLabels = {daily_labels};
const dailyRevenues = {daily_revenues};
const dailyProfits = {daily_profits};

const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
const gridColor = isDark ? 'rgba(255,255,255,.08)' : 'rgba(0,0,0,.07)';
const tickColor = isDark ? '#94a3b8' : '#64748b';
Chart.defaults.color = tickColor;
Chart.defaults.borderColor = gridColor;

// 月別グラフ
let monthlyChart;
function initMonthly(n) {{
  const labels = histLabels.slice(-n);
  const revs = histRevenues.slice(-n);
  const costs = histCosts.slice(-n);
  const profs = histProfits.slice(-n);
  if (monthlyChart) monthlyChart.destroy();
  monthlyChart = new Chart(document.getElementById('monthlyChart'), {{
    data: {{
      labels,
      datasets: [
        {{ type:'bar', label:'収益', data:revs, backgroundColor:'rgba(34,197,94,.7)', yAxisID:'y' }},
        {{ type:'bar', label:'コスト', data:costs, backgroundColor:'rgba(239,68,68,.7)', yAxisID:'y' }},
        {{ type:'line', label:'純利益', data:profs, borderColor:'#a78bfa', backgroundColor:'rgba(167,139,250,.15)',
           tension:.4, fill:true, pointRadius:4, yAxisID:'y' }},
      ]
    }},
    options: {{
      responsive:true, interaction:{{mode:'index',intersect:false}},
      plugins:{{ legend:{{position:'top'}} }},
      scales:{{
        y:{{ ticks:{{ callback:v=>'¥'+v.toLocaleString() }} }},
        x:{{ grid:{{display:false}} }}
      }}
    }}
  }});
}}
initMonthly(3);

function setRange(n, btn) {{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  btn.classList.add('active');
  initMonthly(n);
}}

// 事業別収益
if (bizLabels.length > 0) {{
  new Chart(document.getElementById('bizChart'), {{
    type:'bar',
    data:{{ labels:bizLabels, datasets:[{{ label:'収益', data:bizRevenues,
      backgroundColor:'rgba(99,102,241,.7)', borderRadius:6 }}] }},
    options:{{ indexAxis:'y', responsive:true,
      scales:{{ x:{{ ticks:{{callback:v=>'¥'+v.toLocaleString()}} }} }},
      plugins:{{legend:{{display:false}}}}
    }}
  }});

  // ROI
  const roiColors = bizRois.map(r=>r>=100?'rgba(34,197,94,.7)':r>=0?'rgba(245,158,11,.7)':'rgba(239,68,68,.7)');
  new Chart(document.getElementById('roiChart'), {{
    type:'bar',
    data:{{ labels:bizLabels, datasets:[{{ label:'ROI(%)', data:bizRois,
      backgroundColor:roiColors, borderRadius:6 }}] }},
    options:{{ indexAxis:'y', responsive:true,
      scales:{{ x:{{ ticks:{{callback:v=>v+'%'}} }} }},
      plugins:{{legend:{{display:false}}}}
    }}
  }});
}} else {{
  ['bizChart','roiChart'].forEach(id=>{{
    const c=document.getElementById(id);
    c.parentElement.innerHTML+='<p style="text-align:center;color:var(--muted);padding:24px 0;">事業データなし</p>';
    c.remove();
  }});
}}

// 累積純利益
if (histLabels.length > 0) {{
  new Chart(document.getElementById('cumulativeChart'), {{
    type:'line',
    data:{{ labels:histLabels, datasets:[
      {{ label:'累積純利益', data:cumulative, borderColor:'#a78bfa',
         backgroundColor:'rgba(167,139,250,.2)', fill:true, tension:.4, pointRadius:4 }},
      {{ label:'損益分岐点', data:new Array(histLabels.length).fill(0),
         borderColor:'rgba(239,68,68,.5)', borderDash:[6,4], pointRadius:0, fill:false }},
    ] }},
    options:{{ responsive:true, interaction:{{mode:'index',intersect:false}},
      scales:{{ y:{{ ticks:{{callback:v=>'¥'+v.toLocaleString()}} }} }},
      plugins:{{ legend:{{position:'top'}} }}
    }}
  }});
}}

// 今月日次推移
if (dailyLabels.length > 0) {{
  new Chart(document.getElementById('dailyChart'), {{
    type:'line',
    data:{{ labels:dailyLabels, datasets:[
      {{ label:'日次収益', data:dailyRevenues, borderColor:'#22c55e',
         backgroundColor:'rgba(34,197,94,.1)', fill:true, tension:.4, pointRadius:3 }},
      {{ label:'日次純利益', data:dailyProfits, borderColor:'#6366f1',
         backgroundColor:'rgba(99,102,241,.1)', fill:true, tension:.4, pointRadius:3 }},
    ] }},
    options:{{ responsive:true, interaction:{{mode:'index',intersect:false}},
      scales:{{ y:{{ ticks:{{callback:v=>'¥'+v.toLocaleString()}} }},
               x:{{ grid:{{display:false}} }} }},
      plugins:{{ legend:{{position:'top'}} }}
    }}
  }});
}} else {{
  const el = document.getElementById('dailyChart');
  el.parentElement.innerHTML += '<p style="text-align:center;color:var(--muted);padding:24px 0;">今月のデータ蓄積中です</p>';
  el.remove();
}}
</script>
</body>
</html>"""

    output_path = OUTPUT_DIR / "dashboard.html"
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"[generator] ダッシュボード生成: {output_path}")
    return output_path


# ── エントリーポイント ────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    out = generate()
    print(f"generator: 完了 → {out}")
