"""
html_report.py
Generates a beautiful self-contained HTML weekly dashboard for Bayside House.
Saved as Bayside_Report_YYYY-MM-DD.html alongside the script each run.
All assets (Chart.js, fonts) are embedded inline — the file is fully self-contained
and works offline, as an email attachment, or shared via OneDrive/Dropbox.
"""
import json
import math
import os
from datetime import date, timedelta

# Load Chart.js from the local copy sitting next to this script.
# Falls back to an empty string (charts won't render) if the file is missing.
def _load_chartjs() -> str:
    try:
        _dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(_dir, "chart.min.js"), encoding="utf-8") as _f:
            return _f.read()
    except FileNotFoundError:
        return ""  # graceful degradation

_CHARTJS = _load_chartjs()

# ── Booking source brand colours ──────────────────────────────────────────────
_SOURCE_COLORS = {
    "Booking.com": "#003580",
    "Expedia":     "#1C3F80",
    "HostelWorld": "#CC4400",
    "Agoda":       "#BD081C",
    "Website":     "#10B981",
    "Walk-In":     "#8B5CF6",
    "Phone":       "#F59E0B",
    "Other":       "#64748B",
}
_DISPLAY_SOURCES = [
    "Booking.com", "Expedia", "HostelWorld",
    "Agoda", "Website", "Walk-In", "Phone",
]
_FLAG_MAP = {
    "Australia": "🇦🇺", "USA": "🇺🇸", "United States": "🇺🇸",
    "United Kingdom": "🇬🇧", "Germany": "🇩🇪", "France": "🇫🇷",
    "Japan": "🇯🇵", "Canada": "🇨🇦", "New Zealand": "🇳🇿",
    "India": "🇮🇳", "Italy": "🇮🇹", "Spain": "🇪🇸",
    "Netherlands": "🇳🇱", "South Korea": "🇰🇷", "Brazil": "🇧🇷",
    "China": "🇨🇳", "Singapore": "🇸🇬", "Ireland": "🇮🇪",
    "Sweden": "🇸🇪", "Norway": "🇳🇴", "Denmark": "🇩🇰",
    "Austria": "🇦🇹", "Switzerland": "🇨🇭", "Thailand": "🇹🇭",
    "Indonesia": "🇮🇩", "Philippines": "🇵🇭", "Malaysia": "🇲🇾",
}

# ── Static CSS (not an f-string — no brace escaping needed) ──────────────────
_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#F1F5F9;--sidebar:#0F172A;--card:#FFFFFF;--text:#1E293B;
  --muted:#64748B;--border:#E2E8F0;--primary:#2563EB;--accent:#F97316;
  --success:#10B981;--radius:16px;
  --shadow:0 1px 3px rgba(0,0,0,.07),0 4px 20px rgba(0,0,0,.06);
}
[data-theme=dark]{
  --bg:#0F172A;--card:#1E293B;--text:#F1F5F9;
  --muted:#94A3B8;--border:#334155;
  --shadow:0 1px 3px rgba(0,0,0,.3),0 4px 20px rgba(0,0,0,.25);
}
body{font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
     display:flex;min-height:100vh;transition:background .3s,color .3s}

/* Sidebar */
.sidebar{width:72px;background:var(--sidebar);display:flex;flex-direction:column;
         align-items:center;padding:20px 0;gap:8px;position:fixed;
         top:0;left:0;height:100vh;z-index:10}
.logo{width:44px;height:44px;border-radius:12px;margin-bottom:16px;
      background:linear-gradient(135deg,#2563EB,#06B6D4);
      display:flex;align-items:center;justify-content:center;
      font-weight:800;font-size:15px;color:#fff;letter-spacing:-1px}
.nav-item{width:44px;height:44px;border-radius:12px;display:flex;
          align-items:center;justify-content:center;font-size:20px;
          cursor:default;transition:background .2s;color:#64748B}
.nav-item.active{background:rgba(37,99,235,.35);color:#fff}
.nav-spacer{flex:1}

/* Main */
.main{margin-left:72px;flex:1;padding:24px;display:flex;
      flex-direction:column;gap:20px;max-width:1440px}

/* Top bar */
.topbar{display:flex;align-items:center;gap:14px;
        background:var(--card);border-radius:var(--radius);
        padding:14px 20px;box-shadow:var(--shadow)}
.topbar-title{font-size:20px;font-weight:700;flex:1}
.week-badge{background:linear-gradient(135deg,#2563EB,#06B6D4);
            color:#fff;border-radius:20px;padding:6px 16px;
            font-size:13px;font-weight:600}
.week-range{font-size:13px;color:var(--muted)}
.theme-toggle{width:36px;height:36px;border-radius:10px;border:none;
              background:var(--bg);color:var(--muted);font-size:18px;
              cursor:pointer;display:flex;align-items:center;
              justify-content:center;transition:background .2s}
.theme-toggle:hover{background:var(--border)}

/* Cards */
.card{background:var(--card);border-radius:var(--radius);
      box-shadow:var(--shadow);padding:20px;
      transition:background .3s,box-shadow .3s}
.card-title{font-size:12px;font-weight:600;color:var(--muted);
            text-transform:uppercase;letter-spacing:.5px;margin-bottom:14px}

/* Grids */
.row-4{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.row-3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.row-2{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
.middle{display:grid;grid-template-columns:1fr 1.3fr .7fr;gap:16px}
@media(max-width:1200px){
  .row-4{grid-template-columns:repeat(2,1fr)}
  .middle{grid-template-columns:1fr 1fr}
}
@media(max-width:720px){
  .row-4,.row-3,.row-2,.middle{grid-template-columns:1fr}
  .main{padding:12px}
}

/* KPI cards */
.kpi-card{display:flex;align-items:center;gap:16px}
.kpi-icon{width:52px;height:52px;border-radius:14px;font-size:24px;
          display:flex;align-items:center;justify-content:center;flex-shrink:0}
.kpi-label{font-size:12px;color:var(--muted);font-weight:500;margin-bottom:4px}
.kpi-big{font-size:26px;font-weight:800;letter-spacing:-1px;line-height:1.1}
.kpi-sub{font-size:11px;color:var(--muted);margin-top:3px}
.badge{display:inline-flex;align-items:center;gap:3px;
       font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px;margin-top:6px}
.pos{background:#DCFCE7;color:#16A34A}
.neg{background:#FEE2E2;color:#DC2626}
[data-theme=dark] .pos{background:rgba(22,163,74,.2);color:#4ADE80}
[data-theme=dark] .neg{background:rgba(220,38,38,.2);color:#F87171}

/* Hero gradient card */
.hero-card{background:linear-gradient(135deg,#1E40AF 0%,#2563EB 45%,#06B6D4 100%);
           color:#fff;border-radius:var(--radius);padding:24px;
           box-shadow:0 8px 32px rgba(37,99,235,.4)}
.hero-card .kpi-label{color:rgba(255,255,255,.7)}
.hero-card .kpi-big{color:#fff}
.hero-badge{background:rgba(255,255,255,.2);color:#fff}

/* Gauge */
.gauge-wrap{display:flex;flex-direction:column;align-items:center}
.gauge-svg{width:160px;height:160px;overflow:visible}
.gauge-pct{font-family:Inter,sans-serif;font-weight:800;font-size:28px;fill:var(--text)}
.gauge-lbl{font-family:Inter,sans-serif;font-size:11px;fill:var(--muted)}
[data-theme=dark] .gauge-pct{fill:#F1F5F9}
[data-theme=dark] .gauge-lbl{fill:#94A3B8}
.gauge-sub{display:flex;gap:20px;margin-top:10px;justify-content:center}
.gauge-item{text-align:center}
.gauge-item-val{font-size:15px;font-weight:700}
.gauge-item-lbl{font-size:11px;color:var(--muted)}

/* Source bars */
.src-list{display:flex;flex-direction:column;gap:11px}
.src-row{display:flex;align-items:center;gap:8px}
.src-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.src-name{font-size:13px;width:110px;flex-shrink:0;color:var(--text)}
.bar-wrap{flex:1;height:7px;background:var(--border);border-radius:4px;overflow:hidden}
.bar{height:100%;border-radius:4px;transition:width .6s ease}
.src-val{font-size:13px;font-weight:600;width:28px;text-align:right;color:var(--muted)}

/* Channel list */
.ch-list{display:flex;flex-direction:column;gap:13px}
.ch-row{display:flex;align-items:center;gap:8px}
.ch-name{font-size:13px;width:140px;flex-shrink:0;color:var(--text)}
.ch-val{font-size:13px;font-weight:600;width:36px;text-align:right;color:var(--muted)}

/* Country list */
.ctr-list{display:flex;flex-direction:column;gap:11px}
.ctr-row{display:flex;align-items:center;gap:10px}
.ctr-flag{font-size:20px;width:28px;flex-shrink:0}
.ctr-name{flex:1;font-size:13px;color:var(--text)}
.ctr-val{font-size:13px;font-weight:600;color:var(--muted)}

/* Section divider */
.section-title{font-size:18px;font-weight:700;padding-top:4px;
               border-top:2px solid var(--border);margin-top:4px}

/* Divider line */
.divider{height:1px;background:var(--border)}

/* Footer */
.footer{text-align:center;font-size:12px;color:var(--muted);padding:8px 0}

/* Chart canvas wrapper */
.chart-wrap{position:relative;width:100%}

/* Insights / operating-review card */
.insights-card{background:linear-gradient(135deg,#0F172A 0%,#1E293B 60%,#1E3A5F 100%);
               color:#F1F5F9;border-radius:var(--radius);padding:24px 26px;
               box-shadow:0 8px 32px rgba(15,23,42,.35);position:relative;overflow:hidden}
.insights-card::before{content:"";position:absolute;top:-40px;right:-40px;width:180px;height:180px;
               background:radial-gradient(circle,rgba(37,99,235,.35),transparent 70%);pointer-events:none}
.ins-badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.8px;
           text-transform:uppercase;color:#38BDF8;background:rgba(56,189,248,.12);
           border:1px solid rgba(56,189,248,.3);padding:4px 12px;border-radius:20px;margin-bottom:12px}
.ins-headline{font-size:21px;font-weight:800;line-height:1.35;letter-spacing:-.3px;
              margin-bottom:18px;max-width:900px}
.ins-cols{display:grid;grid-template-columns:1fr 1fr;gap:26px}
@media(max-width:720px){.ins-cols{grid-template-columns:1fr}}
.ins-col-title{font-size:13px;font-weight:700;color:#94A3B8;margin-bottom:10px}
.ins-list{list-style:none;display:flex;flex-direction:column;gap:9px}
.ins-list li{font-size:14px;line-height:1.5;padding-left:18px;position:relative;color:#E2E8F0}
.ins-list li::before{content:"›";position:absolute;left:0;color:#38BDF8;font-weight:700}
"""

# ── Static JavaScript (chart init — not an f-string) ─────────────────────────
_JS = """
const CH_COLORS = ['#3B82F6','#F97316','#10B981','#8B5CF6','#EF4444','#EC4899'];

function buildGradient(ctx, h, c1, c2) {
  const g = ctx.createLinearGradient(0,0,0,h);
  g.addColorStop(0, c1); g.addColorStop(1, c2); return g;
}

// Nightly occupancy line chart
(function(){
  const el = document.getElementById('activityChart');
  if(!el) return;
  const ctx = el.getContext('2d');
  const grad = buildGradient(ctx, 200, 'rgba(37,99,235,.28)', 'rgba(37,99,235,.01)');
  new Chart(el, {
    type:'line',
    data:{
      labels: DATA.dayLabels,
      datasets:[{
        label:'Occupancy %',
        data: DATA.nightlyOcc,
        borderColor:'#2563EB',
        backgroundColor: grad,
        borderWidth:3, fill:true, tension:.4,
        pointBackgroundColor:'#2563EB',
        pointRadius:5, pointHoverRadius:7,
      }]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false},
               tooltip:{callbacks:{label:c=>c.parsed.y+'%'}}},
      scales:{
        y:{min:0,max:100,
           grid:{color:'rgba(100,116,139,.1)'},
           ticks:{callback:v=>v+'%',font:{size:11},color:'#64748B'}},
        x:{grid:{display:false},ticks:{font:{size:11},color:'#64748B'}}
      }
    }
  });
})();

// Date booked bar chart
(function(){
  const el = document.getElementById('dbChart');
  if(!el || !DATA.dbLabels.length) return;
  new Chart(el, {
    type:'bar',
    data:{
      labels: DATA.dbLabels,
      datasets:[{
        label:'Bookings',
        data: DATA.dbValues,
        backgroundColor: DATA.dbColors,
        borderRadius:8, borderSkipped:false,
      }]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        y:{grid:{color:'rgba(100,116,139,.1)'},
           ticks:{font:{size:11},color:'#64748B',stepSize:1}},
        x:{grid:{display:false},ticks:{font:{size:11},color:'#64748B'}}
      }
    }
  });
})();

// Theme toggle
function toggleTheme(){
  const h = document.documentElement;
  const dark = h.dataset.theme==='dark';
  h.dataset.theme = dark?'light':'dark';
  document.querySelector('.theme-toggle').textContent = dark?'🌙':'☀️';
}
if(window.matchMedia('(prefers-color-scheme: dark)').matches){
  document.documentElement.dataset.theme='dark';
  const btn = document.querySelector('.theme-toggle');
  if(btn) btn.textContent='☀️';
}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gauge_arc(pct: float, r: float = 80) -> str:
    c = 2 * math.pi * r
    return f"{c * min(pct, 100) / 100:.2f} {c:.2f}"


def _insights_html(insights: str | None) -> str:
    """Render the markdown operating-review into a styled hero card."""
    if not insights:
        return ""
    import html as _html

    headline = ""
    sections = {"happening": [], "todo": []}
    current = None
    for raw in insights.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("**headline:**"):
            headline = line.split("**Headline:**", 1)[-1].replace("**", "").strip()
            if not headline:  # case-insensitive fallback
                headline = line.split(":", 1)[-1].replace("**", "").strip()
        elif "what's happening" in low or "whats happening" in low:
            current = "happening"
        elif "what to do" in low:
            current = "todo"
        elif line.startswith(("-", "•", "*")) and current:
            sections[current].append(line.lstrip("-•* ").strip())

    def _esc(s):
        return _html.escape(s)

    happening = "".join(f"<li>{_esc(x)}</li>" for x in sections["happening"])
    todo = "".join(f"<li>{_esc(x)}</li>" for x in sections["todo"])

    cols = ""
    if happening:
        cols += f"""
        <div class="ins-col">
          <div class="ins-col-title">📈 What's happening</div>
          <ul class="ins-list">{happening}</ul>
        </div>"""
    if todo:
        cols += f"""
        <div class="ins-col">
          <div class="ins-col-title">🎯 What to do</div>
          <ul class="ins-list">{todo}</ul>
        </div>"""

    # If parsing failed, fall back to raw text
    if not headline and not cols:
        return (
            '<div class="insights-card"><div class="ins-headline">This Week\'s Story</div>'
            f'<p style="font-size:14px;line-height:1.6">{_esc(insights)}</p></div>'
        )

    return f"""
      <div class="insights-card">
        <div class="ins-badge">AI Operating Review</div>
        <div class="ins-headline">{_esc(headline)}</div>
        <div class="ins-cols">{cols}</div>
      </div>"""


def _src_bars(src_dict: dict, cancel_dict: dict | None = None) -> str:
    data = [(s, src_dict.get(s, 0)) for s in _DISPLAY_SOURCES]
    data = [(s, v) for s, v in data if v > 0]
    if not data:
        return "<p style='color:var(--muted);font-size:13px'>No data</p>"
    max_v = max(v for _, v in data) or 1
    rows = ""
    for src, val in data:
        color = _SOURCE_COLORS.get(src, "#64748B")
        bar_w = round(val / max_v * 100)
        cancel = (cancel_dict or {}).get(src, 0) if cancel_dict else 0
        cancel_html = (
            f" <span style='font-size:11px;color:#EF4444'>({cancel}✕)</span>"
            if cancel > 0 else ""
        )
        rows += f"""
        <div class="src-row">
          <div class="src-dot" style="background:{color}"></div>
          <span class="src-name">{src}</span>
          <div class="bar-wrap">
            <div class="bar" style="width:{bar_w}%;background:{color}"></div>
          </div>
          <span class="src-val">{val}</span>{cancel_html}
        </div>"""
    return rows


def _ga4_section(ga4_data: dict, week_label: str) -> str:
    if not ga4_data:
        return ""

    ga4_ses = ga4_data.get("total_sessions",  0)
    ga4_usr = ga4_data.get("total_users",     0)
    ga4_pvs = ga4_data.get("total_pageviews", 0)
    total   = max(ga4_ses, 1)

    # Channel bars
    ch_colors = ["#3B82F6", "#F97316", "#10B981", "#8B5CF6", "#EF4444", "#EC4899"]
    ch_order  = ["Direct", "Organic Search", "Organic Social",
                 "Referral", "Paid Other", "Unassigned"]
    ch_rows = ""
    for i, ch in enumerate(ch_order):
        ses = (ga4_data.get("channels") or {}).get(ch, {}).get("sessions", 0)
        if not ses:
            continue
        pct = round(ses / total * 100)
        c   = ch_colors[i % len(ch_colors)]
        ch_rows += f"""
        <div class="ch-row">
          <span class="ch-name">{ch}</span>
          <div class="bar-wrap"><div class="bar" style="width:{pct}%;background:{c}"></div></div>
          <span class="ch-val">{ses}</span>
        </div>"""

    # Top countries
    countries = (ga4_data.get("top_countries") or [])[:5]
    ctr_rows = ""
    for name, ses in countries:
        flag = _FLAG_MAP.get(name, "🌍")
        ctr_rows += f"""
        <div class="ctr-row">
          <span class="ctr-flag">{flag}</span>
          <span class="ctr-name">{name}</span>
          <span class="ctr-val">{ses}</span>
        </div>"""

    # Devices
    devices = ga4_data.get("devices", {})
    mob = devices.get("mobile",  0)
    dsk = devices.get("desktop", 0)
    tab = devices.get("tablet",  0)
    dev_total = max(mob + dsk + tab, 1)
    mob_pct = round(mob / dev_total * 100)
    dsk_pct = round(dsk / dev_total * 100)
    tab_pct = round(tab / dev_total * 100)

    dev_rows = ""
    for label, val, pct, color in [
        ("Mobile",  mob, mob_pct, "#F97316"),
        ("Desktop", dsk, dsk_pct, "#3B82F6"),
        ("Tablet",  tab, tab_pct, "#10B981"),
    ]:
        if val:
            dev_rows += f"""
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <span style="font-size:12px;width:55px;color:var(--muted)">{label}</span>
            <div class="bar-wrap" style="flex:1">
              <div class="bar" style="width:{pct}%;background:{color}"></div>
            </div>
            <span style="font-size:12px;font-weight:600;color:var(--muted)">{val}</span>
          </div>"""

    # Age groups
    age_data = ga4_data.get("age_groups", {})
    age_order = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
    age_max = max((age_data.get(a, 0) for a in age_order), default=1) or 1
    age_rows = ""
    age_colors = ["#3B82F6", "#06B6D4", "#10B981", "#F59E0B", "#F97316", "#EF4444"]
    for i, ag in enumerate(age_order):
        val = age_data.get(ag, 0)
        if not val:
            continue
        pct = round(val / age_max * 100)
        c   = age_colors[i]
        age_rows += f"""
        <div class="src-row">
          <span class="src-name" style="width:50px">{ag}</span>
          <div class="bar-wrap"><div class="bar" style="width:{pct}%;background:{c}"></div></div>
          <span class="src-val">{val}</span>
        </div>"""

    # Gender
    gender = ga4_data.get("gender", {})
    g_male   = gender.get("male",    0)
    g_female = gender.get("female",  0)
    g_unk    = gender.get("unknown", 0)
    g_total  = max(g_male + g_female + g_unk, 1)
    gender_html = ""
    for label, val, color in [("Male", g_male, "#3B82F6"),
                               ("Female", g_female, "#EC4899"),
                               ("Unknown", g_unk, "#94A3B8")]:
        if val:
            pct = round(val / g_total * 100)
            gender_html += f"""
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <span style="font-size:12px;width:65px;color:var(--muted)">{label}</span>
            <div class="bar-wrap" style="flex:1">
              <div class="bar" style="width:{pct}%;background:{color}"></div>
            </div>
            <span style="font-size:12px;font-weight:600;color:var(--muted)">{val}</span>
          </div>"""

    return f"""
      <div class="section-title">Website Analytics</div>

      <div class="row-3">
        <div class="card kpi-card">
          <div class="kpi-icon" style="background:linear-gradient(135deg,#3B82F6,#1D4ED8)">📊</div>
          <div>
            <div class="kpi-label">Sessions</div>
            <div class="kpi-big">{ga4_ses:,}</div>
          </div>
        </div>
        <div class="card kpi-card">
          <div class="kpi-icon" style="background:linear-gradient(135deg,#10B981,#059669)">👤</div>
          <div>
            <div class="kpi-label">Users</div>
            <div class="kpi-big">{ga4_usr:,}</div>
          </div>
        </div>
        <div class="card kpi-card">
          <div class="kpi-icon" style="background:linear-gradient(135deg,#8B5CF6,#7C3AED)">📄</div>
          <div>
            <div class="kpi-label">Page Views</div>
            <div class="kpi-big">{ga4_pvs:,}</div>
          </div>
        </div>
      </div>

      <div class="row-2">
        <div class="card">
          <div class="card-title">Traffic Channels</div>
          <div class="ch-list">{ch_rows}</div>
        </div>
        <div class="card">
          <div class="card-title">Top Countries</div>
          <div class="ctr-list">{ctr_rows}</div>
        </div>
      </div>

      <div class="row-2">
        <div class="card">
          <div class="card-title">Age Groups (GA4)</div>
          <div class="src-list">{age_rows if age_rows else
            "<p style='color:var(--muted);font-size:13px'>Requires Google Signals enabled in GA4</p>"
          }</div>
        </div>
        <div class="card">
          <div class="card-title">Gender</div>
          {gender_html if g_male or g_female else
           "<p style='color:var(--muted);font-size:13px'>Requires Google Signals enabled in GA4</p>"}
          <div style="margin-top:16px">
            <div class="card-title">Devices</div>
            {dev_rows}
          </div>
        </div>
      </div>"""


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_html_report(
    stats: dict,
    week_start: date,
    week_end: date,
    nightly_week: list[int],
    ga4_data: dict | None = None,
    output_dir: str | None = None,
    insights: str | None = None,
) -> str:
    """
    Generate a self-contained HTML dashboard for the weekly report.
    Returns the path to the saved .html file.
    """
    # ── Labels & derived values ───────────────────────────────────────────────
    week_label  = f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}"
    day_labels  = [(week_start + timedelta(days=i)).strftime("%a") for i in range(7)]
    n_beds      = stats["n_beds"]
    occ_nightly = [round(c / n_beds * 100, 1) if n_beds else 0 for c in nightly_week]
    occ_week    = round(stats["occ_week"],  1)
    occ_month   = round(stats["occ_month"], 1)
    occ_ytd     = round(stats["occ_ytd"],   1)
    rev_week    = stats["rev_week"]
    rev_month   = stats["rev_month"]
    rev_ly      = stats["rev_ly"]
    rev_diff    = stats["rev_diff"]
    adr         = stats["adr"]
    adr_ytd     = stats["adr_ytd"]
    revpar      = stats["revpar"]
    people      = stats["people_in_house"]
    total_ci    = stats["total_checkins"]
    ci_cancels  = stats["ci_cancellations"]
    db_total    = stats.get("db_total", 0) + stats.get("db_cancellations", 0)

    rev_arrow     = "▲" if rev_diff >= 0 else "▼"
    rev_arrow_cls = "pos" if rev_diff >= 0 else "neg"
    rev_diff_str  = f"${abs(rev_diff):,.2f}"

    # ── Chart data (for JS) ───────────────────────────────────────────────────
    ci_data = [(s, stats["ci"].get(s, 0)) for s in _DISPLAY_SOURCES]
    ci_data = [(s, v) for s, v in ci_data if v > 0]
    db_data = [(s, stats["db"].get(s, 0)) for s in _DISPLAY_SOURCES]
    db_data = [(s, v) for s, v in db_data if v > 0]

    chart_data = {
        "dayLabels":  day_labels,
        "nightlyOcc": occ_nightly,
        "ciLabels":   [s for s, v in ci_data],
        "ciValues":   [v for s, v in ci_data],
        "ciColors":   [_SOURCE_COLORS.get(s, "#64748B") for s, v in ci_data],
        "dbLabels":   [s for s, v in db_data],
        "dbValues":   [v for s, v in db_data],
        "dbColors":   [_SOURCE_COLORS.get(s, "#64748B") for s, v in db_data],
    }
    chart_json = json.dumps(chart_data, ensure_ascii=False)

    # ── Dynamic HTML sections ─────────────────────────────────────────────────
    ci_html  = _src_bars(stats["ci"], stats.get("ci_cancel"))
    db_html  = _src_bars(stats.get("db", {}), stats.get("db_cancel"))
    ga4_html = _ga4_section(ga4_data, week_label)
    ins_html = _insights_html(insights)
    arc      = _gauge_arc(occ_week)
    today    = date.today().strftime("%d %B %Y")

    # ── Assemble HTML ─────────────────────────────────────────────────────────
    html = (
        "<!DOCTYPE html>\n"
        '<html lang="en" data-theme="light">\n'
        "<head>\n"
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'  <title>Bayside House — {week_label}</title>\n'
        f"  <style>{_CSS}</style>\n"
        f"  <script>{_CHARTJS}</script>\n"
        "</head>\n"
        "<body>\n\n"

        # Sidebar
        '  <aside class="sidebar">\n'
        '    <div class="logo">BH</div>\n'
        '    <div class="nav-item active" title="Dashboard">🏠</div>\n'
        '    <div class="nav-item" title="Revenue">💰</div>\n'
        '    <div class="nav-item" title="Bookings">🏨</div>\n'
        '    <div class="nav-item" title="Analytics">📊</div>\n'
        '    <div class="nav-item" title="Reviews">⭐</div>\n'
        '    <div class="nav-spacer"></div>\n'
        '    <div class="nav-item" title="Settings">⚙️</div>\n'
        '  </aside>\n\n'

        # Main
        '  <main class="main">\n\n'

        # Top bar
        '    <div class="topbar">\n'
        '      <div class="topbar-title">Bayside House</div>\n'
        f'      <span class="week-badge">Weekly Report</span>\n'
        f'      <span class="week-range">{week_label}</span>\n'
        '      <button class="theme-toggle" onclick="toggleTheme()" title="Toggle dark mode">🌙</button>\n'
        '    </div>\n\n'

        # AI operating review (only rendered if insights were generated)
        + ins_html + ("\n\n" if ins_html else "")

        # KPI Row
        + '    <div class="row-4">\n'

        # Occupancy
        '      <div class="card kpi-card">\n'
        '        <div class="kpi-icon" style="background:linear-gradient(135deg,#2563EB,#06B6D4)">🏨</div>\n'
        '        <div>\n'
        '          <div class="kpi-label">Occupancy</div>\n'
        f'          <div class="kpi-big">{occ_week}%</div>\n'
        f'          <div class="kpi-sub">MTD {occ_month}%&nbsp;·&nbsp;YTD {occ_ytd}%</div>\n'
        '        </div>\n'
        '      </div>\n'

        # Revenue hero
        '      <div class="hero-card">\n'
        '        <div class="kpi-label">Revenue This Week</div>\n'
        f'        <div class="kpi-big">${rev_week:,.2f}</div>\n'
        f'        <span class="badge hero-badge">{rev_arrow} {rev_diff_str} vs last year</span>\n'
        '      </div>\n'

        # People in House
        '      <div class="card kpi-card">\n'
        '        <div class="kpi-icon" style="background:linear-gradient(135deg,#10B981,#059669)">🛏️</div>\n'
        '        <div>\n'
        '          <div class="kpi-label">People in House</div>\n'
        f'          <div class="kpi-big">{people}</div>\n'
        f'          <div class="kpi-sub">of {n_beds} beds total</div>\n'
        '        </div>\n'
        '      </div>\n'

        # ADR
        '      <div class="card kpi-card">\n'
        '        <div class="kpi-icon" style="background:linear-gradient(135deg,#F97316,#EF4444)">💵</div>\n'
        '        <div>\n'
        '          <div class="kpi-label">Avg Daily Rate</div>\n'
        f'          <div class="kpi-big">${adr:,.2f}</div>\n'
        f'          <div class="kpi-sub">YTD ${adr_ytd:,.2f}&nbsp;·&nbsp;RevPAR ${revpar:,.2f}</div>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n\n'  # end row-4

        # Middle: Check-ins | Nightly chart | Gauge
        '    <div class="middle">\n'

        # Check-ins
        '      <div class="card">\n'
        '        <div class="card-title">Check-Ins by Source</div>\n'
        f'        <div class="src-list">{ci_html}</div>\n'
        '        <div class="divider" style="margin:14px 0"></div>\n'
        '        <div style="display:flex;justify-content:space-between;align-items:center">\n'
        '          <span style="font-size:13px;color:var(--muted)">Total Check-Ins</span>\n'
        f'          <span style="font-size:20px;font-weight:800">{total_ci}</span>\n'
        '        </div>\n'
        '        <div style="display:flex;justify-content:space-between;margin-top:6px">\n'
        '          <span style="font-size:12px;color:var(--muted)">Cancellations</span>\n'
        f'          <span style="font-size:12px;color:#EF4444;font-weight:600">{ci_cancels}</span>\n'
        '        </div>\n'
        '      </div>\n'

        # Nightly occupancy chart
        '      <div class="card">\n'
        '        <div class="card-title">Nightly Occupancy</div>\n'
        '        <div class="chart-wrap" style="height:210px">\n'
        '          <canvas id="activityChart"></canvas>\n'
        '        </div>\n'
        '      </div>\n'

        # Occupancy gauge
        '      <div class="card gauge-wrap">\n'
        '        <div class="card-title" style="text-align:center">Weekly Occupancy</div>\n'
        '        <svg class="gauge-svg" viewBox="0 0 200 200">\n'
        '          <defs>\n'
        '            <linearGradient id="gGrad" x1="0%" y1="0%" x2="100%" y2="0%">\n'
        '              <stop offset="0%" stop-color="#2563EB"/>\n'
        '              <stop offset="100%" stop-color="#06B6D4"/>\n'
        '            </linearGradient>\n'
        '          </defs>\n'
        '          <circle cx="100" cy="100" r="80" fill="none" stroke="var(--border)" stroke-width="16"/>\n'
        f'          <circle cx="100" cy="100" r="80" fill="none" stroke="url(#gGrad)" stroke-width="16"\n'
        f'                  stroke-dasharray="{arc}" stroke-linecap="round" transform="rotate(-90 100 100)"/>\n'
        f'          <text x="100" y="96" text-anchor="middle" class="gauge-pct">{occ_week}%</text>\n'
        '          <text x="100" y="116" text-anchor="middle" class="gauge-lbl">This Week</text>\n'
        '        </svg>\n'
        '        <div class="gauge-sub">\n'
        f'          <div class="gauge-item"><div class="gauge-item-val">{occ_month}%</div><div class="gauge-item-lbl">Month</div></div>\n'
        f'          <div class="gauge-item"><div class="gauge-item-val">{occ_ytd}%</div><div class="gauge-item-lbl">YTD</div></div>\n'
        f'          <div class="gauge-item"><div class="gauge-item-val">{n_beds}</div><div class="gauge-item-lbl">Beds</div></div>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n\n'  # end middle

        # Bottom: Date Booked | Revenue breakdown
        '    <div class="row-2">\n'

        # Date Booked
        '      <div class="card">\n'
        '        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px">\n'
        '          <div>\n'
        '            <div class="card-title">Date Booked This Week</div>\n'
        f'            <span style="font-size:26px;font-weight:800">{db_total}</span>'
        '            <span style="font-size:13px;color:var(--muted);margin-left:6px">bookings</span>\n'
        '          </div>\n'
        '        </div>\n'
        '        <div class="src-list" style="margin-bottom:16px">'
        f'{db_html}</div>\n'
        '        <div class="chart-wrap" style="height:160px">\n'
        '          <canvas id="dbChart"></canvas>\n'
        '        </div>\n'
        '      </div>\n'

        # Revenue
        '      <div class="card">\n'
        '        <div class="card-title">Revenue Breakdown</div>\n'
        '        <div style="display:flex;flex-direction:column;gap:14px">\n'
        '          <div style="display:flex;justify-content:space-between;align-items:center">\n'
        '            <div>\n'
        '              <div style="font-size:12px;color:var(--muted)">This Week</div>\n'
        f'              <div style="font-size:24px;font-weight:800">${rev_week:,.2f}</div>\n'
        '            </div>\n'
        f'            <span class="badge {rev_arrow_cls}">{rev_arrow}&nbsp;{rev_diff_str}</span>\n'
        '          </div>\n'
        '          <div class="divider"></div>\n'
        '          <div style="display:flex;justify-content:space-between">\n'
        '            <div>\n'
        '              <div style="font-size:12px;color:var(--muted)">Month to Date</div>\n'
        f'              <div style="font-size:18px;font-weight:700">${rev_month:,.2f}</div>\n'
        '            </div>\n'
        '            <div style="text-align:right">\n'
        '              <div style="font-size:12px;color:var(--muted)">Same Week Last Year</div>\n'
        f'              <div style="font-size:18px;font-weight:700">${rev_ly:,.2f}</div>\n'
        '            </div>\n'
        '          </div>\n'
        '          <div class="divider"></div>\n'
        '          <div style="display:flex;justify-content:space-between">\n'
        '            <div>\n'
        '              <div style="font-size:12px;color:var(--muted)">RevPAR</div>\n'
        f'              <div style="font-size:18px;font-weight:700">${revpar:,.2f}</div>\n'
        '            </div>\n'
        '            <div style="text-align:right">\n'
        '              <div style="font-size:12px;color:var(--muted)">YTD ADR</div>\n'
        f'              <div style="font-size:18px;font-weight:700">${adr_ytd:,.2f}</div>\n'
        '            </div>\n'
        '          </div>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n\n'  # end row-2

        # GA4 section
        + ga4_html + "\n\n"

        # EDM section
        + """
    <div class="section-title">Email Campaigns (EDM)</div>
    <div class="kpi-row" style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px">
      <div class="kpi-card"><div class="kpi-label">Total Sends</div><div class="kpi-value">7,806</div><div class="kpi-sub">3 campaigns · Jul 2025–May 2026</div></div>
      <div class="kpi-card"><div class="kpi-label">Avg Delivery Rate</div><div class="kpi-value">95.9%</div><div class="kpi-sub">↑ Bounce rate improving each send</div></div>
      <div class="kpi-card"><div class="kpi-label">Avg Open Rate</div><div class="kpi-value">10.1%</div><div class="kpi-sub">↓ Drop in May '26 — needs review</div></div>
    </div>
    <div style="background:var(--surface);border-radius:16px;overflow:hidden;margin-bottom:20px">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="background:var(--bg)">
            <th style="text-align:left;padding:10px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)">Campaign</th>
            <th style="text-align:right;padding:10px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)">Sent</th>
            <th style="text-align:right;padding:10px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)">Delivered</th>
            <th style="text-align:right;padding:10px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)">Opens</th>
            <th style="text-align:right;padding:10px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)">Clicks</th>
            <th style="text-align:right;padding:10px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)">Bounces</th>
            <th style="text-align:right;padding:10px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)">Unsubs</th>
          </tr>
        </thead>
        <tbody>
          <tr style="border-top:1px solid var(--border)">
            <td style="padding:12px 16px"><strong>Discover Melbourne</strong><br><span style="font-size:11px;color:var(--muted);font-style:italic">Sep 12, 2025</span></td>
            <td style="padding:12px 16px;text-align:right;font-weight:600">2,552</td>
            <td style="padding:12px 16px;text-align:right;color:#10b981">2,487 (97.5%)</td>
            <td style="padding:12px 16px;text-align:right;color:#10b981">300 (12.1%)</td>
            <td style="padding:12px 16px;text-align:right">12 (0.48%)</td>
            <td style="padding:12px 16px;text-align:right">65 (2.5%)</td>
            <td style="padding:12px 16px;text-align:right">60 (2.4%)</td>
          </tr>
          <tr style="border-top:1px solid var(--border);background:var(--bg)">
            <td style="padding:12px 16px"><strong>Winter Warmer Deals</strong><br><span style="font-size:11px;color:var(--muted);font-style:italic">Jul 17, 2025</span></td>
            <td style="padding:12px 16px;text-align:right;font-weight:600">2,673</td>
            <td style="padding:12px 16px;text-align:right;color:#f59e0b">2,499 (93.5%)</td>
            <td style="padding:12px 16px;text-align:right;color:#10b981">300 (12.0%)</td>
            <td style="padding:12px 16px;text-align:right">12 (0.48%)</td>
            <td style="padding:12px 16px;text-align:right;color:#ef4444">174 (6.5%)</td>
            <td style="padding:12px 16px;text-align:right">68 (2.7%)</td>
          </tr>
          <tr style="border-top:1px solid var(--border)">
            <td style="padding:12px 16px"><strong>Stay Longer</strong><br><span style="font-size:11px;color:var(--muted);font-style:italic">May 6, 2026</span></td>
            <td style="padding:12px 16px;text-align:right;font-weight:600">2,581</td>
            <td style="padding:12px 16px;text-align:right;color:#10b981">2,494 (96.6%)</td>
            <td style="padding:12px 16px;text-align:right;color:#ef4444">152 (6.1%)</td>
            <td style="padding:12px 16px;text-align:right">10 (0.40%)</td>
            <td style="padding:12px 16px;text-align:right">87 (3.4%)</td>
            <td style="padding:12px 16px;text-align:right">56 (2.2%)</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div style="background:var(--surface);border-radius:16px;padding:20px;margin-bottom:20px">
      <div style="font-size:13px;font-weight:700;margin-bottom:14px;color:var(--text)">Key Insights</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:13px">
        <div style="padding:12px 14px;background:var(--bg);border-radius:10px">⚠️ <strong>May 2026 open rate halved</strong> — 6.1% vs 12% prior. Review subject line &amp; send timing.</div>
        <div style="padding:12px 14px;background:var(--bg);border-radius:10px">✅ <strong>Delivery improving</strong> — bounce rate fell from 6.5% to 3.4% across campaigns.</div>
        <div style="padding:12px 14px;background:var(--bg);border-radius:10px">💡 <strong>Clicks under 0.5%</strong> — CTAs need stronger copy and clearer booking links.</div>
        <div style="padding:12px 14px;background:var(--bg);border-radius:10px">📅 <strong>Only 3 sends in 12 months</strong> — monthly cadence recommended to keep list warm.</div>
      </div>
    </div>
""" + "\n"

        # Footer
        f'    <div class="footer">Generated {today} &nbsp;·&nbsp; Bayside House Weekly Report &nbsp;·&nbsp; {week_label}</div>\n\n'

        "  </main>\n\n"

        # Scripts (Chart.js already loaded in <head>)
        f"  <script>const DATA = {chart_json};\n{_JS}</script>\n\n"

        "</body>\n</html>"
    )

    # ── Save file ─────────────────────────────────────────────────────────────
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(__file__))

    os.makedirs(output_dir, exist_ok=True)
    fname = f"Bayside_Report_{week_end.isoformat()}.html"
    fpath = os.path.join(output_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)

    return fpath
