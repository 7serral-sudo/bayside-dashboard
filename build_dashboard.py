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
DATA_2025_PATH = os.path.join(SCRIPT_DIR, "data_2025_reference.json")

# Bed count used for the 2025 comparison line -- 2025's true historical bed
# count isn't tracked anywhere, so we apply the value known to have been in
# effect for nearly all of 2026 (83, before the mid-July change to 84) for a
# self-consistent approximation. This only affects the muted "last year"
# overlay line, never the primary 2026 figures (which use each week's real
# contemporaneous bed count).
LY_N_BEDS = 83

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
    """Returns list of dicts: date, ci_total, people_in_house, adr, adr_ytd,
    adr_mtd, long_termers, db_total, sources{} (CI by source, for channel charts)."""
    rows = _values(service, sheet_id, f"{sheets_client.PERF_TAB}!A3:AL2000")
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
            "adr_mtd":           _fnum(r, sheets_client.ADR_MTD_COL),
            "long_termers":      _fnum(r, sheets_client.LONG_TERMERS_COL),
            "db_total":          _fnum(r, sheets_client.DB_TOTAL_COL),
            "last_min_bk":       _fnum(r, sheets_client.DB_LASTMIN_COL),
            "sources":           {},
        }
        for i, src in enumerate(sheets_client.DISPLAY_SOURCES):
            col = sheets_client.CI_SRC_START + i * 2
            week["sources"][src] = _fnum(r, col)
        out.append(week)
    return out


def load_2025_reference():
    """Cached full-year 2025 daily occupancy/revenue + monthly check-ins.
    See fetch_2025_reference.py. Returns None if the cache hasn't been built."""
    if not os.path.exists(DATA_2025_PATH):
        return None
    with open(DATA_2025_PATH, encoding="utf-8") as f:
        return json.load(f)


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
    """Aggregates every week's GA4 row that falls within the most recent
    week's calendar month, so the dashboard shows monthly totals rather
    than a single week's (smaller, noisier) numbers. Sessions/pageviews
    sum cleanly; "Users" becomes a sum-of-weekly-users approximation since
    GA4 weekly snapshots can't be de-duplicated into a true unique-monthly
    count after the fact."""
    rows = _values(service, sheet_id, f"{sheets_client.WEB_TAB}!A3:AK2000")
    if not rows:
        return None

    last_date = _date_str(rows[-1][0])
    try:
        target_month, target_year = last_date.split("/")[1], last_date.split("/")[2]
    except IndexError:
        target_month, target_year = None, None

    month_rows = [
        r for r in rows
        if _date_str(r[0]).split("/")[1:] == [target_month, target_year]
    ] if target_month else [rows[-1]]

    sessions = sum(_fnum(r, 1) for r in month_rows)
    users = sum(_fnum(r, 2) for r in month_rows)
    pageviews = sum(_fnum(r, 3) for r in month_rows)

    channels = {ch: 0.0 for ch in sheets_client.WEB_CHANNELS}
    for r in month_rows:
        for i, ch in enumerate(sheets_client.WEB_CHANNELS):
            channels[ch] += _fnum(r, sheets_client.WEB_CH_START + i)

    country_totals: dict[str, float] = {}
    for r in month_rows:
        for i in range(sheets_client.TOP_N_COUNTRIES):
            c = sheets_client.WEB_CTR_START + i * 2
            name = _fstr(r, c)
            if name:
                country_totals[name] = country_totals.get(name, 0.0) + _fnum(r, c + 1)
    countries = sorted(country_totals.items(), key=lambda kv: kv[1], reverse=True)[:sheets_client.TOP_N_COUNTRIES]

    devices = {dv: 0.0 for dv in sheets_client.DEVICES}
    for r in month_rows:
        for i, dv in enumerate(sheets_client.DEVICES):
            devices[dv] += _fnum(r, sheets_client.WEB_DEV_START + i)

    return {
        "month_label": f"{sheets_client.MONTHS[int(target_month) - 1]} {target_year}" if target_month else last_date,
        "weeks_included": len(month_rows),
        "sessions":   sessions,
        "users":      users,
        "pageviews":  pageviews,
        "channels":   channels,
        "countries":  countries,
        "devices":    devices,
    }


# ---------------------------------------------------------------------------
# 2025 reference-data helpers
# ---------------------------------------------------------------------------

def ly_range_sums(ref: dict, start: date, end: date) -> tuple[int, float]:
    """(accommodations_booked_sum, revenue_sum) for a 2025 date range from the cache."""
    daily = ref["daily"]
    booked, rev = 0, 0.0
    cur = start
    while cur <= end:
        day = daily.get(cur.isoformat())
        if day:
            booked += day["accommodations_booked"]
            rev += day["revenue"]
        cur += timedelta(days=1)
    return booked, rev


def ly_equivalent_week(week_end_2026: date) -> tuple[date, date]:
    """Same-weekday week in 2025, 364 days (52 weeks) earlier."""
    ly_end = week_end_2026 - timedelta(days=364)
    return ly_end - timedelta(days=6), ly_end


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


# ---------------------------------------------------------------------------
# Build dynamic HTML blocks
# ---------------------------------------------------------------------------

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


def build_channels_chart_data(perf_weeks, current_year, n_months=6):
    """Grouped bar chart data: each channel's confirmed CI count per month,
    over the last n_months that have occurred this year."""
    months_with_data = []
    for m_idx in range(1, 13):
        month_abbr = sheets_client.MONTHS[m_idx - 1]
        has_data = any(
            w["date"].split("/")[1] == f"{m_idx:02d}" and w["date"].endswith(str(current_year))
            for w in perf_weeks
        )
        if has_data:
            months_with_data.append(month_abbr)
    months_with_data = months_with_data[-n_months:]

    per_month_totals = {m: {} for m in months_with_data}
    for w in perf_weeks:
        try:
            dd, mm, yyyy = w["date"].split("/")
        except ValueError:
            continue
        if yyyy != str(current_year):
            continue
        month_abbr = sheets_client.MONTHS[int(mm) - 1]
        if month_abbr not in per_month_totals:
            continue
        for src, count in w["sources"].items():
            per_month_totals[month_abbr][src] = per_month_totals[month_abbr].get(src, 0) + count

    grand_totals = {}
    for m_totals in per_month_totals.values():
        for src, count in m_totals.items():
            grand_totals[src] = grand_totals.get(src, 0) + count
    top_sources = [s for s, _ in sorted(grand_totals.items(), key=lambda kv: kv[1], reverse=True)[:5]]

    datasets = []
    for i, src in enumerate(top_sources):
        color = SOURCE_COLORS[i % len(SOURCE_COLORS)]
        data = [int(per_month_totals[m].get(src, 0)) for m in months_with_data]
        datasets.append({
            "label": SOURCE_DISPLAY.get(src, src),
            "data": data,
            "backgroundColor": color,
            "borderRadius": 3,
            "borderWidth": 0,
        })
    return months_with_data, datasets


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
    week_end_date = datetime.strptime(week_end_str, "%d/%m/%Y").date()
    current_year = int(week_end_str.split("/")[-1])

    occ_monthly = {w["month"]: w["month_occ"] for w in occ_weeks if w["month"] and w["month_occ"] is not None}
    ref_2025 = load_2025_reference()

    # -- This Week KPIs (vs last week) ---------------------------------------
    prev_occ  = occ_weeks[-2] if len(occ_weeks) > 1 else None
    prev_perf = perf_weeks[-2] if len(perf_weeks) > 1 else None

    occ_week_pct = f'{latest_occ["week_occ"]:.1f}%'
    occ_lastweek_pct = f'{prev_occ["week_occ"]:.1f}%' if prev_occ else 'n/a'
    adr_week     = fmt_money(latest_perf["adr"])
    adr_lastweek = fmt_money(prev_perf["adr"]) if prev_perf else 'n/a'
    checkins_week = f'{int(latest_perf["ci_total"]):,}'
    checkins_lastweek = f'{int(prev_perf["ci_total"]):,}' if prev_perf else 'n/a'
    week_bookings = f'{int(latest_perf["last_min_bk"]):,}'
    long_termers = f'{int(latest_perf["long_termers"]):,}'

    # -- This Month KPIs (vs last month) -------------------------------------
    current_month_abbr = sheets_client.MONTHS[week_end_date.month - 1]
    occ_month_val = occ_monthly.get(current_month_abbr)
    occ_month_pct = f'{occ_month_val:.1f}%' if occ_month_val is not None else 'n/a'
    month_idx = sheets_client.MONTHS.index(current_month_abbr)
    prev_month_abbr = sheets_client.MONTHS[month_idx - 1] if month_idx > 0 else None
    prev_month_occ = occ_monthly.get(prev_month_abbr) if prev_month_abbr else None
    occ_lastmonth_pct = f'{prev_month_occ:.1f}%' if prev_month_occ is not None else 'n/a'
    adr_month = fmt_money(latest_perf["adr_mtd"])

    # -- Revenue ----------------------------------------------------------
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

    # -- This Year KPIs (vs last year, via the 2025 reference cache) --------
    occ_ytd_pct  = f'{latest_occ["ytd_occ"]:.1f}%'
    adr_ytd      = fmt_money(latest_perf["adr_ytd"])
    if ref_2025:
        ly_ytd_end = date(current_year - 1, week_end_date.month, week_end_date.day)
        ly_booked, ly_rev = ly_range_sums(ref_2025, date(current_year - 1, 1, 1), ly_ytd_end)
        ly_days = (ly_ytd_end - date(current_year - 1, 1, 1)).days + 1
        ly_occ_ytd = ly_booked / (LY_N_BEDS * ly_days) * 100 if ly_days else 0
        ly_adr_ytd = (ly_rev / ly_booked) if ly_booked else 0
        occ_lastyear_pct = f'{ly_occ_ytd:.1f}%'
        adr_lastyear = fmt_money(ly_adr_ytd)
    else:
        occ_lastyear_pct = 'n/a'
        adr_lastyear = 'n/a'

    # -- Reviews ------------------------------------------------------------
    reviews = reviews or {"google": 0, "booking": 0, "hostelworld": 0, "expedia": 0}

    # -- Website analytics ----------------------------------------------------
    if web:
        web_title = f'Website Analytics ({web["month_label"]})'
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

    if ref_2025:
        occ_chart_data_ly, adr_chart_data_ly = [], []
        for w in occ_weeks:
            try:
                d = datetime.strptime(w["date"], "%d/%m/%Y").date()
            except ValueError:
                occ_chart_data_ly.append(None)
                adr_chart_data_ly.append(None)
                continue
            ly_start, ly_end = ly_equivalent_week(d)
            booked, rev = ly_range_sums(ref_2025, ly_start, ly_end)
            occ_chart_data_ly.append(round(booked / (LY_N_BEDS * 7) * 100, 1))
            adr_chart_data_ly.append(round(rev / booked, 2) if booked else None)
    else:
        occ_chart_data_ly = [None] * len(occ_weeks)
        adr_chart_data_ly = [None] * len(occ_weeks)

    # Monthly check-ins, this year vs last year
    ci_by_month = {m: 0 for m in sheets_client.MONTHS}
    for w in perf_weeks:
        try:
            dd, mm, yyyy = w["date"].split("/")
            if yyyy != str(current_year):
                continue
            ci_by_month[sheets_client.MONTHS[int(mm) - 1]] += w["ci_total"]
        except ValueError:
            continue
    months_occurred = [m for m in sheets_client.MONTHS if m in occ_monthly or ci_by_month.get(m, 0) > 0]
    ci_chart_labels = months_occurred
    ci_chart_data = [int(ci_by_month.get(m, 0)) for m in months_occurred]
    if ref_2025:
        ly_monthly_ci = ref_2025.get("monthly_checkins", {})
        ci_chart_data_ly = [int(ly_monthly_ci.get(str(sheets_client.MONTHS.index(m) + 1), 0)) for m in months_occurred]
    else:
        ci_chart_data_ly = [None] * len(months_occurred)

    channels_chart_labels, channels_chart_datasets = build_channels_chart_data(perf_weeks, current_year)

    # -- Build HTML blocks -------------------------------------------------
    monthly_cards_html = build_monthly_cards_html(occ_monthly, revenue, perf_weeks, current_year)

    footer_text = f"Bayside House Dashboard · Week {week_number} of 52 · Year-to-date data through {fmt_date_human(week_end_str)}"

    # -- Token replacement ---------------------------------------------------
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    tokens = {
        "__OCC_WEEK_PCT__":        occ_week_pct,
        "__OCC_LASTWEEK_PCT__":    occ_lastweek_pct,
        "__ADR_WEEK__":            adr_week,
        "__ADR_LASTWEEK__":        adr_lastweek,
        "__CHECKINS_WEEK__":       checkins_week,
        "__CHECKINS_LASTWEEK__":   checkins_lastweek,
        "__WEEK_BOOKINGS__":       week_bookings,
        "__LONG_TERMERS__":        long_termers,
        "__OCC_MONTH_PCT__":       occ_month_pct,
        "__OCC_LASTMONTH_PCT__":   occ_lastmonth_pct,
        "__ADR_MONTH__":           adr_month,
        "__OCC_YTD_PCT__":         occ_ytd_pct,
        "__OCC_LASTYEAR_PCT__":    occ_lastyear_pct,
        "__ADR_YTD__":             adr_ytd,
        "__ADR_LASTYEAR__":        adr_lastyear,
        "__YTD_REVENUE__":         fmt_money_k(ytd_revenue),
        "__YTD_REVENUE_WEEK_LABEL__": ytd_revenue_label,
        "__MTD_REVENUE__":         fmt_money_k(mtd_revenue),
        "__MTD_MONTH_LABEL__":     f"({current_month_abbr})",
        "__MTD_YOY_LABEL__":       mtd_yoy_label,
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
        "__OCC_CHART_DATA_LY__":  json.dumps(occ_chart_data_ly),
        "__ADR_CHART_LABELS__":   json.dumps(adr_chart_labels),
        "__ADR_CHART_DATA__":    json.dumps(adr_chart_data),
        "__ADR_CHART_DATA_LY__": json.dumps(adr_chart_data_ly),
        "__CI_CHART_LABELS__":   json.dumps(ci_chart_labels),
        "__CI_CHART_DATA__":     json.dumps(ci_chart_data),
        "__CI_CHART_DATA_LY__":  json.dumps(ci_chart_data_ly),
        "__CHANNELS_CHART_LABELS__":   json.dumps(channels_chart_labels),
        "__CHANNELS_CHART_DATASETS__": json.dumps(channels_chart_datasets),
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
