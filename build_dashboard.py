"""
build_dashboard.py
Regenerates Bayside_Dashboard.html (the live, password-protected Vercel site)
from data already written to the Google Sheet by weekly_report.py.

Run standalone:   python build_dashboard.py
Or import and call build() from weekly_report.py after sheets_client.write_report().
"""
import os
import json
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

import sheets_client

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH  = os.path.join(SCRIPT_DIR, "dashboard_template.html")
OUTPUT_PATH    = os.path.join(SCRIPT_DIR, "Bayside_Dashboard.html")

# Review *counts* aren't tracked anywhere in the sheet (only ratings are).
# Update these by hand when they change; ratings below always come live from the sheet.
REVIEW_COUNTS = {
    "google": 272,
    "booking": 511,
    "hostelworld": 82,
    "expedia": 55,
}

SOURCE_DISPLAY = {
    "Booking.com": "Booking.com",
    "Expedia":     "Expedia",
    "HW":          "Hostelworld",
    "Agoda":       "Agoda",
    "Website":     "Website",
    "Walk + Ph":   "Walk-in & Phone",
}
SOURCE_COLORS = [
    "#5ed29c", "#4ca57a", "#4d7368", "#5a9b8e", "#3f8f6a", "#6b7280",
]


# ---------------------------------------------------------------------------
# Sheet readers
# ---------------------------------------------------------------------------

def _values(service, sheet_id, rng):
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=rng,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    return result.get("values", [])


def _fnum(row, idx, default=0.0):
    try:
        v = row[idx]
        if v in (None, ""):
            return default
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").replace("%", "").strip()
            if v == "":
                return default
        return float(v)
    except (IndexError, ValueError, TypeError):
        return default


def _fstr(row, idx, default=""):
    try:
        v = row[idx]
        return v if v not in (None, "") else default
    except IndexError:
        return default


def _date_str(v) -> str:
    """UNFORMATTED_VALUE returns date cells as a Sheets serial day number
    (epoch 1899-12-30) instead of the DD/MM/YYYY string -- convert it back."""
    if isinstance(v, (int, float)):
        d = date(1899, 12, 30) + timedelta(days=int(v))
        return d.strftime("%d/%m/%Y")
    return str(v) if v else ""


def fetch_occupancy(service, sheet_id):
    """Returns list of dicts: date, week_occ_pct, month, month_occ_pct, ytd_occ_pct."""
    rows = _values(service, sheet_id, f"{sheets_client.OCC_TAB}!A2:E2000")
    out = []
    for r in rows:
        if not r or not r[0]:
            continue
        out.append({
            "date":          _date_str(r[0]),
            "week_occ":      _fnum(r, 1) * 100,
            "month":         _fstr(r, 2),
            "month_occ":     _fnum(r, 3) * 100 if _fstr(r, 3) else None,
            "ytd_occ":       _fnum(r, 4) * 100,
        })
    return out


def fetch_performance(service, sheet_id):
    """Returns list of dicts: date, ci_total, people_in_house, adr, adr_ytd, sources{}."""
    rows = _values(service, sheet_id, f"{sheets_client.PERF_TAB}!A3:AJ2000")
    out = []
    for r in rows:
        if not r or not r[0]:
            continue
        week = {
            "date":              _date_str(r[0]),
            "ci_total":          _fnum(r, sheets_client.CI_TOTAL_COL),
            "people_in_house":   _fnum(r, sheets_client.CI_BEDS_COL),
            "adr":               _fnum(r, sheets_client.ADR_COL),
            "adr_ytd":           _fnum(r, sheets_client.ADR_YTD_COL),
            "sources":           {},
        }
        for i, src in enumerate(sheets_client.DISPLAY_SOURCES):
            col = sheets_client.CI_SRC_START + i * 2
            week["sources"][src] = _fnum(r, col)
        out.append(week)
    return out


def fetch_revenue(service, sheet_id):
    """Returns dict: month_abbr -> {"py": float, "cy": float}."""
    rows = _values(service, sheet_id, f"{sheets_client.REV_TAB}!A2:C13")
    out = {}
    for r in rows:
        if not r or not r[0]:
            continue
        out[r[0]] = {"py": _fnum(r, 1), "cy": _fnum(r, 2)}
    return out


def fetch_platform_reviews(service, sheet_id):
    rows = _values(service, sheet_id, f"{sheets_client.PLAT_TAB}!A2:E2000")
    if not rows:
        return None
    last = rows[-1]
    return {
        "google":      _fnum(last, 1),
        "booking":     _fnum(last, 2),
        "hostelworld": _fnum(last, 3),
        "expedia":     _fnum(last, 4),
    }


def fetch_website_analytics(service, sheet_id):
    rows = _values(service, sheet_id, f"{sheets_client.WEB_TAB}!A3:AK2000")
    if not rows:
        return None
    last = rows[-1]

    channels = {
        ch: _fnum(last, sheets_client.WEB_CH_START + i)
        for i, ch in enumerate(sheets_client.WEB_CHANNELS)
    }
    countries = []
    for i in range(sheets_client.TOP_N_COUNTRIES):
        c = sheets_client.WEB_CTR_START + i * 2
        name = _fstr(last, c)
        if name:
            countries.append((name, _fnum(last, c + 1)))
    devices = {
        dv: _fnum(last, sheets_client.WEB_DEV_START + i)
        for i, dv in enumerate(sheets_client.DEVICES)
    }

    return {
        "week_label": _date_str(last[0]) if last else "",
        "sessions":   _fnum(last, 1),
        "users":      _fnum(last, 2),
        "pageviews":  _fnum(last, 3),
        "channels":   channels,
        "countries":  countries,
        "devices":    devices,
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_money_k(v: float) -> str:
    return f"${v/1000:,.1f}k"


def fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def fmt_date_short(date_str: str) -> str:
    """DD/MM/YYYY -> DD/MM/YY"""
    parts = date_str.split("/")
    if len(parts) != 3:
        return date_str
    dd, mm, yyyy = parts
    return f"{dd}/{mm}/{yyyy[-2:]}"


def fmt_date_human(date_str: str) -> str:
    """DD/MM/YYYY -> '24 Jun 2026'"""
    try:
        d = datetime.strptime(date_str, "%d/%m/%Y").date()
        return d.strftime("%-d %b %Y") if os.name != "nt" else f"{d.day} {d.strftime('%b')} {d.year}"
    except ValueError:
        return date_str


def fmt_date_human_short(date_str: str) -> str:
    """DD/MM/YYYY -> '23 Jun'"""
    try:
        d = datetime.strptime(date_str, "%d/%m/%Y").date()
        return f"{d.day} {d.strftime('%b')}"
    except ValueError:
        return date_str


# ---------------------------------------------------------------------------
# Build dynamic HTML blocks
# ---------------------------------------------------------------------------

def build_top_channels_html(perf_weeks, current_year):
    totals = {}
    for w in perf_weeks:
        try:
            yr = int(w["date"].split("/")[-1])
        except (ValueError, IndexError):
            continue
        if yr != current_year:
            continue
        for src, count in w["sources"].items():
            totals[src] = totals.get(src, 0) + count

    grand_total = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:5]

    rows = []
    for i, (src, count) in enumerate(ranked):
        pct = (count / grand_total * 100) if grand_total else 0
        color = SOURCE_COLORS[i % len(SOURCE_COLORS)]
        label = SOURCE_DISPLAY.get(src, src)
        rows.append(f'''          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
            <div style="width: 32px; height: 32px; background: {color}; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #000; font-weight: 700; font-size: 12px;">{i+1}</div>
            <div style="flex: 1;">
              <div style="font-weight: 600; font-size: 13px; color: #e8f0f5;">{label}</div>
              <div style="font-size: 11px; color: #6b7280;">{pct:.0f}% · {int(count)} check-ins</div>
            </div>
          </div>''')
    if not rows:
        rows.append('          <div style="color: #6b7280; font-size: 13px;">No data yet</div>')
    return "\n".join(rows)


def build_web_channels_html(channels: dict):
    if not channels or not any(channels.values()):
        return '          <div style="color: #6b7280; font-size: 13px;">No data yet</div>'
    rows = []
    for ch, sessions in channels.items():
        rows.append(
            f'          <div style="margin-bottom: 15px;"><span style="color: #94a3b8;">{ch}:</span> '
            f'<span style="color: #5ed29c; font-weight: 700;">{int(sessions)}</span></div>'
        )
    return "\n".join(rows)


def build_web_countries_html(countries: list):
    if not countries:
        return '          <div style="color: #6b7280; font-size: 13px;">No data yet</div>'
    rows = []
    for i, (name, sessions) in enumerate(countries):
        rows.append(
            f'          <div style="margin-bottom: 12px; display: flex; justify-content: space-between;">'
            f'<span style="color: #94a3b8;">{i+1}. {name}</span> '
            f'<span style="color: #5ed29c; font-weight: 700;">{int(sessions)}</span></div>'
        )
    return "\n".join(rows)


def build_web_device_html(devices: dict):
    if not devices or not any(devices.values()):
        return '          <div style="color: #6b7280; font-size: 13px;">No data yet</div>'
    total = sum(devices.values()) or 1
    rows = []
    for dv, count in devices.items():
        pct = count / total * 100
        rows.append(
            f'          <div style="margin-bottom: 15px;"><span style="color: #94a3b8;">{dv}:</span> '
            f'<span style="color: #5ed29c; font-weight: 700;">{int(count)} ({pct:.0f}%)</span></div>'
        )
    return "\n".join(rows)


def build_monthly_cards_html(occ_monthly: dict, revenue: dict, perf_weeks: list, current_year: int):
    # Check-ins per calendar month, derived from each week's date.
    ci_by_month = {m: 0 for m in sheets_client.MONTHS}
    for w in perf_weeks:
        try:
            dd, mm, yyyy = w["date"].split("/")
            if int(yyyy) != current_year:
                continue
            month_abbr = sheets_client.MONTHS[int(mm) - 1]
            ci_by_month[month_abbr] += w["ci_total"]
        except (ValueError, IndexError):
            continue

    cards = []
    for m in sheets_client.MONTHS:
        if m not in occ_monthly:
            continue
        occ_pct = occ_monthly[m]
        ci = int(ci_by_month.get(m, 0))
        rev = revenue.get(m, {}).get("cy", 0)
        cards.append(f'''      <div class="monthly-card">
          <div class="monthly-month">{m}</div>
          <div class="monthly-value">{occ_pct:.2f}%</div>
          <div class="monthly-sub">{ci} check-ins</div>
          <div class="monthly-sub">{fmt_money_k(rev)}</div>
        </div>''')
    if not cards:
        cards.append('      <div style="color: #6b7280; font-size: 13px;">No data yet</div>')
    return "\n".join(cards)


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build(sheet_id: str | None = None, log=print):
    sheet_id = sheet_id or os.environ["GOOGLE_SHEET_ID"]
    service = sheets_client._build_service()

    log("  -> Reading Occupancy tab ...")
    occ_weeks = fetch_occupancy(service, sheet_id)
    log("  -> Reading Performance tab ...")
    perf_weeks = fetch_performance(service, sheet_id)
    log("  -> Reading Revenue tab ...")
    revenue = fetch_revenue(service, sheet_id)
    log("  -> Reading Platform Reviews tab ...")
    reviews = fetch_platform_reviews(service, sheet_id)
    log("  -> Reading Website Analytics tab ...")
    web = fetch_website_analytics(service, sheet_id)

    if not occ_weeks or not perf_weeks:
        raise RuntimeError("No data found in Occupancy/Performance tabs -- has weekly_report.py run yet?")

    latest_occ  = occ_weeks[-1]
    latest_perf = perf_weeks[-1]
    week_end_str = latest_occ["date"]
    current_year = int(week_end_str.split("/")[-1])

    occ_monthly = {w["month"]: w["month_occ"] for w in occ_weeks if w["month"] and w["month_occ"] is not None}

    # -- This Week KPIs --------------------------------------------------
    occ_week_pct = f'{latest_occ["week_occ"]:.1f}%'
    occ_ytd_pct  = f'{latest_occ["ytd_occ"]:.1f}%'
    adr_week     = fmt_money(latest_perf["adr"])
    adr_ytd      = fmt_money(latest_perf["adr_ytd"])
    checkins_week = f'{int(latest_perf["ci_total"]):,}'
    people_in_house = f'{int(latest_perf["people_in_house"]):,}'

    # -- Revenue ----------------------------------------------------------
    current_month_abbr = sheets_client.MONTHS[datetime.strptime(week_end_str, "%d/%m/%Y").month - 1]
    ytd_revenue = sum(v["cy"] for v in revenue.values())
    mtd = revenue.get(current_month_abbr, {"py": 0, "cy": 0})
    mtd_revenue = mtd["cy"]
    mtd_ly = mtd["py"]
    if mtd_ly:
        yoy_pct = (mtd_revenue - mtd_ly) / mtd_ly * 100
        yoy_sign = "+" if yoy_pct >= 0 else ""
        mtd_yoy_label = f'vs {current_month_abbr} {current_year - 1}: {fmt_money_k(mtd_ly)} ({yoy_sign}{yoy_pct:.0f}%)'
    else:
        mtd_yoy_label = f'vs {current_month_abbr} {current_year - 1}: n/a'

    week_number = len(occ_weeks)
    ytd_revenue_label = f"Through week {week_number}"

    ytd_checkins = sum(
        w["ci_total"] for w in perf_weeks
        if w["date"].endswith(str(current_year))
    )

    # -- Reviews ------------------------------------------------------------
    reviews = reviews or {"google": 0, "booking": 0, "hostelworld": 0, "expedia": 0}

    # -- Website analytics ----------------------------------------------------
    if web:
        web_title = f'Website Analytics (Week of {fmt_date_human_short(web["week_label"])})'
        web_sessions = int(web["sessions"])
        web_users = int(web["users"])
        web_pageviews = int(web["pageviews"])
        avg_per_session = (web_pageviews / web_sessions) if web_sessions else 0
        top_channel = max(web["channels"].items(), key=lambda kv: kv[1], default=("--", 0))
        top_channel_pct = (top_channel[1] / web_sessions * 100) if web_sessions else 0
        top_country = web["countries"][0] if web["countries"] else ("--", 0)
        web_channels_html = build_web_channels_html(web["channels"])
        web_countries_html = build_web_countries_html(web["countries"])
        web_device_html = build_web_device_html(web["devices"])
        web_top_source_sub = f'{int(top_channel[1])} sessions ({top_channel_pct:.0f}%)'
        web_top_country_sub = f'{int(top_country[1])} sessions'
    else:
        web_title = "Website Analytics"
        web_sessions = web_users = web_pageviews = 0
        avg_per_session = 0
        top_channel = ("--", 0)
        top_country = ("--", 0)
        web_channels_html = build_web_channels_html({})
        web_countries_html = build_web_countries_html([])
        web_device_html = build_web_device_html({})
        web_top_source_sub = "No data yet"
        web_top_country_sub = "No data yet"

    # -- Chart data -----------------------------------------------------------
    occ_chart_labels = [fmt_date_short(w["date"]) for w in occ_weeks]
    occ_chart_data    = [round(w["week_occ"], 1) for w in occ_weeks]
    adr_chart_labels = [fmt_date_short(w["date"]) for w in perf_weeks]
    adr_chart_data    = [round(w["adr"], 2) for w in perf_weeks]
    ci_chart_labels  = adr_chart_labels
    ci_chart_data     = [int(w["ci_total"]) for w in perf_weeks]

    # -- Build HTML blocks -------------------------------------------------
    top_channels_html = build_top_channels_html(perf_weeks, current_year)
    monthly_cards_html = build_monthly_cards_html(occ_monthly, revenue, perf_weeks, current_year)

    footer_text = f"Bayside House Dashboard · Week {week_number} of 52 · Year-to-date data through {fmt_date_human(week_end_str)}"

    # -- Token replacement ---------------------------------------------------
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    tokens = {
        "__OCC_WEEK_PCT__":        occ_week_pct,
        "__OCC_YTD_PCT__":         occ_ytd_pct,
        "__ADR_WEEK__":            adr_week,
        "__ADR_YTD__":             adr_ytd,
        "__CHECKINS_WEEK__":       checkins_week,
        "__PEOPLE_IN_HOUSE__":     people_in_house,
        "__YTD_REVENUE__":         fmt_money_k(ytd_revenue),
        "__YTD_REVENUE_WEEK_LABEL__": ytd_revenue_label,
        "__MTD_REVENUE__":         fmt_money_k(mtd_revenue),
        "__MTD_MONTH_LABEL__":     f"({current_month_abbr})",
        "__MTD_YOY_LABEL__":       mtd_yoy_label,
        "__YTD_CHECKINS__":        f"{int(ytd_checkins):,}",
        "__TOP_CHANNELS_HTML__":   top_channels_html,
        "__WEB_ANALYTICS_TITLE__": web_title,
        "__WEB_SESSIONS__":        f"{web_sessions:,}",
        "__WEB_USERS__":           f"{web_users:,}",
        "__WEB_PAGEVIEWS__":       f"{web_pageviews:,}",
        "__WEB_AVG_PER_SESSION__": f"{avg_per_session:.1f}",
        "__WEB_TOP_SOURCE__":      top_channel[0],
        "__WEB_TOP_SOURCE_SUB__":  web_top_source_sub,
        "__WEB_TOP_COUNTRY__":     top_country[0],
        "__WEB_TOP_COUNTRY_SUB__": web_top_country_sub,
        "__WEB_CHANNELS_HTML__":   web_channels_html,
        "__WEB_COUNTRIES_HTML__":  web_countries_html,
        "__WEB_DEVICE_HTML__":     web_device_html,
        "__REVIEW_GOOGLE_RATING__":      f'{reviews["google"]:.1f}',
        "__REVIEW_GOOGLE_COUNT__":       str(REVIEW_COUNTS["google"]),
        "__REVIEW_BOOKING_RATING__":     f'{reviews["booking"]:.1f}',
        "__REVIEW_BOOKING_COUNT__":      str(REVIEW_COUNTS["booking"]),
        "__REVIEW_HOSTELWORLD_RATING__": f'{reviews["hostelworld"]:.1f}',
        "__REVIEW_HOSTELWORLD_COUNT__":  str(REVIEW_COUNTS["hostelworld"]),
        "__REVIEW_EXPEDIA_RATING__":     f'{reviews["expedia"]:.1f}',
        "__REVIEW_EXPEDIA_COUNT__":      str(REVIEW_COUNTS["expedia"]),
        "__DASHBOARD_YEAR__":      str(current_year),
        "__MONTHLY_CARDS_HTML__":  monthly_cards_html,
        "__FOOTER_TEXT__":         footer_text,
        "__OCC_CHART_LABELS__":    json.dumps(occ_chart_labels),
        "__OCC_CHART_DATA__":     json.dumps(occ_chart_data),
        "__ADR_CHART_LABELS__":   json.dumps(adr_chart_labels),
        "__ADR_CHART_DATA__":    json.dumps(adr_chart_data),
        "__CI_CHART_LABELS__":   json.dumps(ci_chart_labels),
        "__CI_CHART_DATA__":     json.dumps(ci_chart_data),
    }

    for token, value in tokens.items():
        html = html.replace(token, value)

    remaining = [t for t in tokens if t in html]
    if remaining:
        log(f"  WARNING: unresolved tokens left in output: {remaining}")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"  Dashboard written: {OUTPUT_PATH}")
    return OUTPUT_PATH


if __name__ == "__main__":
    build()
