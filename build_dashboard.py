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
# Single source of truth for room-type names, bed counts and the private/dorm
# split -- the same constants build_room_type_adr.py uses to lay the sheet out,
# so the dashboard can't drift from the columns it's reading.
from build_room_type_adr import ROOM_TYPES, ROOM_TYPE_ORDER

def _cmp_html(label, current, prev, higher_is_better=True, fmt_fn=str):
    """Return a coloured HTML string for a KPI sub-label comparison."""
    if current is None or prev is None:
        return f'{label}: n/a'
    green, red = '#3FCF6E', '#F0564A'
    if current > prev:
        color = green if higher_is_better else red
        arrow = '↑'
    elif current < prev:
        color = red if higher_is_better else green
        arrow = '↓'
    else:
        return f'{label}: {fmt_fn(prev)}'
    return f'{label}: <span style="color:{color}">{arrow} {fmt_fn(prev)}</span>'

def _trend(current, prev):
    """Return 'up', 'down', or 'neutral' for use as a data-trend attribute."""
    if current is None or prev is None:
        return 'neutral'
    if current > prev:
        return 'up'
    if current < prev:
        return 'down'
    return 'neutral'

def _web_delta_html(current, prev):
    """Small inline YoY delta badge used in the Website Analytics breakdown lists."""
    if prev is None or prev == 0:
        return ''
    green, red = '#3FCF6E', '#F0564A'
    diff = current - prev
    pct = diff / prev * 100
    if diff > 0:
        color, arrow = green, '↑'
    elif diff < 0:
        color, arrow = red, '↓'
    else:
        return ' <span style="color:#6b7280;font-size:11px">(=)</span>'
    return f' <span style="color:{color};font-size:11px">({arrow}{abs(pct):.0f}%)</span>'


def _color_yoy(text):
    """Wrap the (+X%) or (-X%) part of a YoY label in a coloured span."""
    if '(+' in text:
        idx = text.index('(+')
        return text[:idx] + f'<span style="color:#3FCF6E">{text[idx:]}</span>'
    if '(-' in text:
        idx = text.index('(-')
        return text[:idx] + f'<span style="color:#F0564A">{text[idx:]}</span>'
    return text

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

# Platform order for the "Platform reviews" row, matching the sheet's column
# order. Ratings and counts both come live from the Platform Reviews tab.
PLATFORM_ORDER = ("google", "booking", "hostelworld", "expedia")

SOURCE_DISPLAY = {
    "Booking.com": "Booking.com",
    "Expedia":     "Expedia",
    "HW":          "Hostelworld",
    "Agoda":       "Agoda",
    "Website":     "Website",
    "Walk + Ph":   "Walk-in & Phone",
}
SOURCE_COLORS = {
    "Booking.com": "#3B82F6",  # blue
    "HW":          "#F97316",  # orange (Hostelworld)
    "Walk + Ph":   "#3FCF6E",  # green (Walk-in & Phone)
    "Website":     "#EAB308",  # yellow
    "Expedia":     "#A855F7",  # purple
    "Agoda":       "#94A3B8",  # fallback grey for any other channel
}
SOURCE_COLOR_FALLBACK = "#94A3B8"


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


def _review_rating(reviews, platform):
    """Rating for the tile, or an em dash when it has never been recorded."""
    value = reviews[platform]["rating"]
    return "—" if value is None else f"{value:.1f}"


def _review_count(reviews, platform):
    value = reviews[platform]["count"]
    return "—" if value is None else f"{value:,}"


def fetch_platform_reviews(service, sheet_id):
    """Latest rating and review count per platform.

    Layout is Date | 4 ratings (B-E) | 4 counts (F-I). Any cell can be blank --
    counts didn't exist on older rows, and a platform whose API is unreachable
    writes a blank rather than a zero -- so each column carries its last known
    value forward instead of collapsing to 0.
    """
    rows = _values(service, sheet_id, f"{sheets_client.PLAT_TAB}!A2:I2000")
    if not rows:
        return None

    latest = {p: {"rating": None, "count": None} for p in PLATFORM_ORDER}
    for row in rows:
        for idx, platform in enumerate(PLATFORM_ORDER):
            rating = _fnum(row, 1 + idx, default=None)
            count = _fnum(row, 5 + idx, default=None)
            if rating is not None:
                latest[platform]["rating"] = rating
            if count is not None:
                latest[platform]["count"] = int(count)
    return latest


# Room Type ADR column layout, as written by build_room_type_adr.py:
#   A Month | B,C Private Rooms summary | D,E Pods summary
#   | (Nights, ADR) per type in ROOM_TYPE_ORDER | All Rooms total.
ROOM_TYPE_DETAIL_START_COL = 5


def _room_type_detail(row):
    """Per-room-type nights and ADR from one Room Type ADR row, in sheet order."""
    types = []
    for idx, rt_id in enumerate(ROOM_TYPE_ORDER):
        name, beds, section = ROOM_TYPES[rt_id]
        col = ROOM_TYPE_DETAIL_START_COL + idx * 2
        nights, adr = _fnum(row, col), _fnum(row, col + 1)
        if not nights and not adr:
            continue          # room type not sold yet this year -- skip the tile
        types.append({"name": name, "beds": beds, "section": section,
                      "nights": nights, "adr": adr})
    return types


def fetch_room_type_adr(service, sheet_id):
    """Returns dict with YTD ADR for private rooms, pods and each individual
    room type, or None if not found."""
    try:
        rows = _values(service, sheet_id, "Room Type ADR!A3:U1000")
        if not rows:
            return None

        monthly_data = {"months": [], "private_adr": [], "pods_adr": []}

        # Find the YTD row and monthly rows
        for r in rows:
            if not r or not r[0]:
                continue
            month = str(r[0]).upper()
            if month == "YTD":
                return {
                    "private_adr": _fnum(r, 2),
                    "pods_adr": _fnum(r, 4),
                    "types": _room_type_detail(r),
                    "monthly": monthly_data,
                }
            # Add monthly data
            if len(r) >= 5:
                monthly_data["months"].append(r[0])
                monthly_data["private_adr"].append(_fnum(r, 2))
                monthly_data["pods_adr"].append(_fnum(r, 4))

        return None
    except Exception as exc:
        return None


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

    # Same calendar month last year, for a "vs Jul 2025" YoY comparison
    # (2025 GA4 data was backfilled into this tab -- see backfill_web_analytics_2025.py).
    #
    # Last year's window is trimmed to the same number of weeks the current
    # month has so far. A month still in progress otherwise gets measured
    # against last year's COMPLETED month, which reads as a collapse in
    # traffic when it's only a shorter window: one week of Aug 2026 (330
    # sessions) against five weeks of Aug 2025 (687) showed as -52%.
    prev_sessions = prev_users = prev_pageviews = None
    prev_month_label = None
    ly_rows: list = []
    if target_month:
        ly_year = int(target_year) - 1
        ly_month_rows = [r for r in rows if _date_str(r[0]).split("/")[1:] == [target_month, str(ly_year)]]
        ly_rows = ly_month_rows[:len(month_rows)]
        if ly_rows:
            prev_sessions = sum(_fnum(r, 1) for r in ly_rows)
            prev_users = sum(_fnum(r, 2) for r in ly_rows)
            prev_pageviews = sum(_fnum(r, 3) for r in ly_rows)
            month_name = sheets_client.MONTHS[int(target_month) - 1]
            if len(ly_rows) < len(ly_month_rows):
                n = len(ly_rows)
                prev_month_label = f"same {n} wk{'' if n == 1 else 's'} of {month_name} {ly_year}"
            else:
                prev_month_label = f"{month_name} {ly_year}"

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

    # Same-month-last-year breakdowns, for YoY deltas on each channel/country/device row.
    ly_channels = {ch: 0.0 for ch in sheets_client.WEB_CHANNELS}
    ly_country_totals: dict[str, float] = {}
    ly_devices = {dv: 0.0 for dv in sheets_client.DEVICES}
    for r in ly_rows:
        for i, ch in enumerate(sheets_client.WEB_CHANNELS):
            ly_channels[ch] += _fnum(r, sheets_client.WEB_CH_START + i)
        for i in range(sheets_client.TOP_N_COUNTRIES):
            c = sheets_client.WEB_CTR_START + i * 2
            name = _fstr(r, c)
            if name:
                ly_country_totals[name] = ly_country_totals.get(name, 0.0) + _fnum(r, c + 1)
        for i, dv in enumerate(sheets_client.DEVICES):
            ly_devices[dv] += _fnum(r, sheets_client.WEB_DEV_START + i)

    return {
        "month_label": f"{sheets_client.MONTHS[int(target_month) - 1]} {target_year}" if target_month else last_date,
        "weeks_included": len(month_rows),
        "sessions":   sessions,
        "users":      users,
        "pageviews":  pageviews,
        "channels":   channels,
        "countries":  countries,
        "devices":    devices,
        "prev_month_label": prev_month_label,
        "prev_sessions":    prev_sessions,
        "prev_users":       prev_users,
        "prev_pageviews":   prev_pageviews,
        "ly_channels":      ly_channels if ly_rows else None,
        "ly_countries":     ly_country_totals if ly_rows else None,
        "ly_devices":       ly_devices if ly_rows else None,
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

def build_web_channels_html(channels: dict, ly_channels: dict | None = None):
    if not channels or not any(channels.values()):
        return '          <div>No data yet</div>'
    rows = []
    for ch, sessions in channels.items():
        delta = _web_delta_html(sessions, ly_channels.get(ch) if ly_channels else None)
        rows.append(
            f'          <div>{ch}: <strong style="color:#3FCF6E">{int(sessions)}</strong>{delta}</div>'
        )
    return "\n".join(rows)


def build_web_countries_html(countries: list, ly_countries: dict | None = None):
    if not countries:
        return '          <div>No data yet</div>'
    rows = []
    for i, (name, sessions) in enumerate(countries):
        delta = _web_delta_html(sessions, ly_countries.get(name) if ly_countries else None)
        rows.append(
            f'          <div style="display:flex;justify-content:space-between">'
            f'<span>{i+1}. {name}</span> '
            f'<span><strong style="color:#3FCF6E">{int(sessions)}</strong>{delta}</span></div>'
        )
    return "\n".join(rows)


def build_web_device_html(devices: dict, ly_devices: dict | None = None):
    if not devices or not any(devices.values()):
        return '          <div>No data yet</div>'
    total = sum(devices.values()) or 1
    rows = []
    for dv, count in devices.items():
        pct = count / total * 100
        delta = _web_delta_html(count, ly_devices.get(dv) if ly_devices else None)
        rows.append(
            f'          <div>{dv}: <strong style="color:#3FCF6E">{int(count)} ({pct:.0f}%)</strong>{delta}</div>'
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
        color = SOURCE_COLORS.get(src, SOURCE_COLOR_FALLBACK)
        data = [int(per_month_totals[m].get(src, 0)) for m in months_with_data]
        datasets.append({
            "label": SOURCE_DISPLAY.get(src, src),
            "data": data,
            "backgroundColor": color,
            "borderRadius": 3,
            "borderWidth": 0,
        })
    return months_with_data, datasets


def build_monthly_cards_html(occ_monthly: dict, revenue: dict, perf_weeks: list, current_year: int, ref_2025: dict = None):
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

        # YoY comparisons
        yoy_text = ""
        if ref_2025:
            month_num = sheets_client.MONTHS.index(m) + 1
            ly_monthly_ci = ref_2025.get("monthly_checkins", {})
            ly_ci = int(ly_monthly_ci.get(str(month_num), 0))
            ly_rev = revenue.get(m, {}).get("py", 0)

            if ly_ci:
                ci_pct = ((ci - ly_ci) / ly_ci * 100)
                ci_sign = "+" if ci_pct >= 0 else ""
                ci_color = '#3FCF6E' if ci_pct >= 0 else '#F0564A'
                ci_yoy = f' <span style="color:{ci_color}">({ci_sign}{ci_pct:.0f}%)</span>'
            else:
                ci_yoy = " (n/a)"

            if ly_rev:
                rev_pct = ((rev - ly_rev) / ly_rev * 100)
                rev_sign = "+" if rev_pct >= 0 else ""
                rev_color = '#3FCF6E' if rev_pct >= 0 else '#F0564A'
            else:
                rev_pct, rev_sign, rev_color = 0, "", "#888888"

            if ly_ci:
                occ_part = f'<span style="color:{ci_color}">{ci_sign}{ci_pct:.0f}% occ</span> · '
            else:
                occ_part = ""

            yoy_text = f'<div class="monthly-sub" style="font-size: 11px; margin-top: 4px;">vs \'{str(current_year - 1)[2:]}: {occ_part}<span style="color:{rev_color}">{rev_sign}{rev_pct:.0f}% rev</span></div>'

        cards.append(f'''      <div class="monthly-card">
          <div class="monthly-month">{m}</div>
          <div class="monthly-value">{occ_pct:.1f}%</div>
          <div class="monthly-sub">{fmt_money_k(rev)}</div>
          {yoy_text}
        </div>''')
    if not cards:
        cards.append('      <div style="color: #6b7280; font-size: 13px;">No data yet</div>')
    return "\n".join(cards)


def _add_room_type_occupancy(types, week_end_date, current_year):
    """Adds a YTD occupancy % per room type: nights sold / (beds x days elapsed
    this year). Mutates each type dict in place."""
    days_elapsed = (week_end_date - date(current_year, 1, 1)).days + 1
    for t in types:
        avail = t["beds"] * days_elapsed
        t["occ"] = (t["nights"] / avail * 100) if avail else 0.0


def build_room_type_cards_html(types):
    """Per-room-type YTD ADR tiles, split into private rooms and pods so the
    breakdown reads as the two headline cards above it, itemised."""
    if not types:
        return ""

    groups = []
    for label, section in (("Private rooms", "private"), ("Pods", "dorm")):
        members = [t for t in types if t["section"] == section]
        if not members:
            continue
        tiles = "\n".join(
            f'''          <div class="room-card">
            <div class="room-name">{t["name"]}</div>
            <div class="room-adr num">{fmt_money(t["adr"])}</div>
            <div class="room-occ num">{t.get("occ", 0):.1f}% occupancy</div>
            <div class="room-sub">{int(t["nights"]):,} nights · {t["beds"]} {"beds" if t["beds"] > 1 else "room"}</div>
          </div>'''
            for t in members
        )
        groups.append(f'''      <div>
        <h3 class="room-group-label">{label}</h3>
        <div class="room-grid">
{tiles}
        </div>
      </div>''')

    if not groups:
        return ""
    inner = "\n".join(groups)
    return f'    <div class="room-types">\n{inner}\n    </div>'


def _prior_week(weeks, log, tab_name):
    """The most recent week before the latest one, skipping rows that repeat the
    latest week's date.

    Taking weeks[-2] blindly makes a duplicated row compare the week against
    ITSELF, and because _cmp_html treats equal values as "no change" that
    renders as a flat sub-label with no arrow -- silently wrong rather than
    visibly broken. This shipped once: occupancy read "vs last week: 67.0%"
    against its own 67.0% while the real prior week was 73.6%, hiding a
    6.6-point drop. Duplicates are logged so the weekly run surfaces them, and
    if every earlier row is a duplicate we return None (rendering "n/a")
    rather than inventing a comparison.
    """
    if len(weeks) < 2:
        return None
    latest_date = weeks[-1]["date"]
    for row in reversed(weeks[:-1]):
        if row["date"] != latest_date:
            return row
        log(f"  !! {tab_name}: duplicate row for {latest_date} -- "
            f"excluded from the week-on-week comparison.")
    return None


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
    log("  -> Reading Room Type ADR tab ...")
    room_type_adr = fetch_room_type_adr(service, sheet_id)

    if not occ_weeks or not perf_weeks:
        raise RuntimeError("No data found in Occupancy/Performance tabs -- has weekly_report.py run yet?")

    latest_occ  = occ_weeks[-1]
    latest_perf = perf_weeks[-1]
    week_end_str = latest_occ["date"]
    week_end_date = datetime.strptime(week_end_str, "%d/%m/%Y").date()
    current_year = int(week_end_str.split("/")[-1])

    if room_type_adr and room_type_adr.get("types"):
        _add_room_type_occupancy(room_type_adr["types"], week_end_date, current_year)

    occ_monthly = {w["month"]: w["month_occ"] for w in occ_weeks if w["month"] and w["month_occ"] is not None}
    ref_2025 = load_2025_reference()

    # -- This Week KPIs (vs last week) ---------------------------------------
    prev_occ  = _prior_week(occ_weeks, log, sheets_client.OCC_TAB)
    prev_perf = _prior_week(perf_weeks, log, sheets_client.PERF_TAB)

    occ_week_pct = f'{latest_occ["week_occ"]:.1f}%'
    occ_lastweek_pct = _cmp_html('vs last week', latest_occ["week_occ"],
                                  prev_occ["week_occ"] if prev_occ else None,
                                  fmt_fn=lambda v: f'{v:.1f}%')
    adr_week     = fmt_money(latest_perf["adr"])
    adr_lastweek = _cmp_html('vs last week', latest_perf["adr"],
                              prev_perf["adr"] if prev_perf else None,
                              fmt_fn=fmt_money)
    checkins_week = f'{int(latest_perf["ci_total"]):,}'
    checkins_lastweek = _cmp_html('vs last week', latest_perf["ci_total"],
                                   prev_perf["ci_total"] if prev_perf else None,
                                   fmt_fn=lambda v: f'{int(v):,}')
    week_bookings = f'{int(latest_perf["last_min_bk"]):,}'
    total_week_bookings = f'{int(latest_perf["db_total"]):,}'
    long_termers = f'{int(latest_perf["long_termers"]):,}'

    # -- This Month KPIs (vs last month and vs last year) -------------------
    current_month_abbr = sheets_client.MONTHS[week_end_date.month - 1]
    occ_month_val = occ_monthly.get(current_month_abbr)
    occ_month_pct = f'{occ_month_val:.1f}%' if occ_month_val is not None else 'n/a'
    month_idx = sheets_client.MONTHS.index(current_month_abbr)
    prev_month_abbr = sheets_client.MONTHS[month_idx - 1] if month_idx > 0 else None
    prev_month_occ = occ_monthly.get(prev_month_abbr) if prev_month_abbr else None
    occ_lastmonth_pct = f'{prev_month_occ:.1f}%' if prev_month_occ is not None else 'n/a'
    adr_month = fmt_money(latest_perf["adr_mtd"])

    # Compare with same month last year (from 2025 reference data)
    occ_month_ly = 'n/a'
    adr_month_ly = 'n/a'
    if ref_2025:
        ly_month_start = date(current_year - 1, week_end_date.month, 1)
        if week_end_date.month == 12:
            ly_month_end = date(current_year - 1, 12, 31)
        else:
            ly_month_end = date(current_year, week_end_date.month + 1, 1) - timedelta(days=1)
        ly_booked, ly_rev = ly_range_sums(ref_2025, ly_month_start, ly_month_end)
        ly_days = (ly_month_end - ly_month_start).days + 1
        if ly_days and ly_booked:
            ly_occ_month = ly_booked / (LY_N_BEDS * ly_days) * 100
            ly_adr_month = ly_rev / ly_booked
            occ_month_ly = _cmp_html('vs last year', occ_month_val, ly_occ_month,
                                      fmt_fn=lambda v: f'{v:.1f}%')
            adr_month_ly = _cmp_html('vs last year', latest_perf["adr_mtd"], ly_adr_month,
                                      fmt_fn=fmt_money)

    # -- Revenue ----------------------------------------------------------
    ytd_revenue = sum(v["cy"] for v in revenue.values())
    mtd = revenue.get(current_month_abbr, {"py": 0, "cy": 0})
    mtd_revenue = mtd["cy"]
    mtd_ly = mtd["py"]
    if mtd_ly:
        yoy_pct = (mtd_revenue - mtd_ly) / mtd_ly * 100
        yoy_sign = "+" if yoy_pct >= 0 else ""
        mtd_yoy_label = _color_yoy(f'vs {current_month_abbr} {current_year - 1}: {fmt_money_k(mtd_ly)} ({yoy_sign}{yoy_pct:.0f}%)')
    else:
        mtd_yoy_label = f'vs {current_month_abbr} {current_year - 1}: n/a'

    week_number = len(occ_weeks)

    # YTD Revenue YoY comparison
    ytd_revenue_label = "n/a"
    if ref_2025:
        ly_ytd_end = date(current_year - 1, week_end_date.month, week_end_date.day)
        ly_booked, ly_rev = ly_range_sums(ref_2025, date(current_year - 1, 1, 1), ly_ytd_end)
        if ly_rev:
            yoy_revenue_pct = (ytd_revenue - ly_rev) / ly_rev * 100
            yoy_sign = "+" if yoy_revenue_pct >= 0 else ""
            ytd_revenue_label = _color_yoy(f"vs {current_year - 1}: {fmt_money_k(ly_rev)} ({yoy_sign}{yoy_revenue_pct:.0f}%)")
        else:
            ytd_revenue_label = f"Through week {week_number}"
    else:
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
        occ_lastyear_pct = _cmp_html('vs last year', latest_occ["ytd_occ"], ly_occ_ytd,
                                      fmt_fn=lambda v: f'{v:.1f}%')
        adr_lastyear = _cmp_html('vs last year', latest_perf["adr_ytd"], ly_adr_ytd,
                                  fmt_fn=fmt_money)
    else:
        occ_lastyear_pct = 'n/a'
        adr_lastyear = 'n/a'

    # -- Room Type ADR --------------------------------------------------
    if room_type_adr:
        private_adr = fmt_money(room_type_adr["private_adr"])
        pods_adr = fmt_money(room_type_adr["pods_adr"])
        room_type_cards_html = build_room_type_cards_html(room_type_adr.get("types", []))
        # Build chart data for room type ADR trends
        room_adr_monthly = room_type_adr.get("monthly", {})
        room_adr_labels = room_adr_monthly.get("months", [])
        room_adr_private_data = [round(v, 2) for v in room_adr_monthly.get("private_adr", [])]
        room_adr_pods_data = [round(v, 2) for v in room_adr_monthly.get("pods_adr", [])]
    else:
        private_adr = 'n/a'
        pods_adr = 'n/a'
        room_type_cards_html = ""
        room_adr_labels = []
        room_adr_private_data = []
        room_adr_pods_data = []

    # -- Reviews ------------------------------------------------------------
    reviews = reviews or {}
    reviews = {p: reviews.get(p) or {"rating": None, "count": None} for p in PLATFORM_ORDER}
    for platform, vals in reviews.items():
        if vals["rating"] is None or vals["count"] is None:
            log(f"  !! Platform reviews: no {platform} "
                f"{'rating' if vals['rating'] is None else 'count'} in the sheet yet.")

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
        ly_channels_map = web.get("ly_channels") or {}
        ly_countries_map = web.get("ly_countries") or {}
        web_channels_html = build_web_channels_html(web["channels"], web.get("ly_channels"))
        web_countries_html = build_web_countries_html(web["countries"], web.get("ly_countries"))
        web_device_html = build_web_device_html(web["devices"], web.get("ly_devices"))
        web_top_source_sub = f'{int(top_channel[1])} sessions ({top_channel_pct:.0f}%){_web_delta_html(top_channel[1], ly_channels_map.get(top_channel[0]))}'
        web_top_country_sub = f'{int(top_country[1])} sessions{_web_delta_html(top_country[1], ly_countries_map.get(top_country[0]))}'
        web_sessions_cmp = _cmp_html(f'vs {web["prev_month_label"]}', web_sessions, web["prev_sessions"], fmt_fn=lambda v: f'{int(v):,}')
        web_pageviews_cmp = _cmp_html(f'vs {web["prev_month_label"]}', web_pageviews, web["prev_pageviews"], fmt_fn=lambda v: f'{int(v):,}')
        trend_web_sessions = _trend(web_sessions, web["prev_sessions"])
        trend_web_pageviews = _trend(web_pageviews, web["prev_pageviews"])
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
        web_sessions_cmp = "No prior month yet"
        web_pageviews_cmp = "No prior month yet"
        trend_web_sessions = "neutral"
        trend_web_pageviews = "neutral"

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
    monthly_cards_html = build_monthly_cards_html(occ_monthly, revenue, perf_weeks, current_year, ref_2025)

    footer_text = f"Bayside House Dashboard · Week {week_number} of 52 · Year-to-date data through {fmt_date_human(week_end_str)}"

    # -- Token replacement ---------------------------------------------------
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    tokens = {
        "__TREND_OCC_WEEK__":      _trend(latest_occ["week_occ"], prev_occ["week_occ"] if prev_occ else None),
        "__TREND_ADR_WEEK__":      _trend(latest_perf["adr"], prev_perf["adr"] if prev_perf else None),
        "__TREND_CI_WEEK__":       _trend(latest_perf["ci_total"], prev_perf["ci_total"] if prev_perf else None),
        "__TREND_OCC_MONTH__":     _trend(occ_month_val, ly_occ_month if ref_2025 and ly_booked else None),
        "__TREND_ADR_MONTH__":     _trend(latest_perf["adr_mtd"], ly_adr_month if ref_2025 and ly_booked else None),
        "__TREND_REV_MONTH__":     _trend(mtd_revenue, mtd_ly if mtd_ly else None),
        "__TREND_OCC_YTD__":       _trend(latest_occ["ytd_occ"], ly_occ_ytd if ref_2025 else None),
        "__TREND_ADR_YTD__":       _trend(latest_perf["adr_ytd"], ly_adr_ytd if ref_2025 else None),
        "__TREND_REV_YTD__":       _trend(ytd_revenue, ly_rev if ref_2025 and ly_rev else None),
        "__OCC_WEEK_PCT__":        occ_week_pct,
        "__OCC_LASTWEEK_PCT__":    occ_lastweek_pct,
        "__ADR_WEEK__":            adr_week,
        "__ADR_LASTWEEK__":        adr_lastweek,
        "__CHECKINS_WEEK__":       checkins_week,
        "__CHECKINS_LASTWEEK__":   checkins_lastweek,
        "__WEEK_BOOKINGS__":       week_bookings,
        "__TOTAL_WEEK_BOOKINGS__": total_week_bookings,
        "__LONG_TERMERS__":        long_termers,
        "__OCC_MONTH_PCT__":       occ_month_pct,
        "__OCC_LASTMONTH_PCT__":   occ_lastmonth_pct,
        "__OCC_MONTH_LY_PCT__":    occ_month_ly,
        "__ADR_MONTH__":           adr_month,
        "__ADR_MONTH_LY__":        adr_month_ly,
        "__OCC_YTD_PCT__":         occ_ytd_pct,
        "__OCC_LASTYEAR_PCT__":    occ_lastyear_pct,
        "__ADR_YTD__":             adr_ytd,
        "__ADR_LASTYEAR__":        adr_lastyear,
        "__PRIVATE_ROOM_ADR_YTD__": private_adr,
        "__PODS_ADR_YTD__":        pods_adr,
        "__ROOM_TYPE_CARDS_HTML__": room_type_cards_html,
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
        "__WEB_SESSIONS_CMP__":    web_sessions_cmp,
        "__WEB_PAGEVIEWS_CMP__":   web_pageviews_cmp,
        "__TREND_WEB_SESSIONS__":  trend_web_sessions,
        "__TREND_WEB_PAGEVIEWS__": trend_web_pageviews,
        "__WEB_TOP_SOURCE__":      top_channel[0],
        "__WEB_TOP_SOURCE_SUB__":  web_top_source_sub,
        "__WEB_TOP_COUNTRY__":     top_country[0],
        "__WEB_TOP_COUNTRY_SUB__": web_top_country_sub,
        "__WEB_CHANNELS_HTML__":   web_channels_html,
        "__WEB_COUNTRIES_HTML__":  web_countries_html,
        "__WEB_DEVICE_HTML__":     web_device_html,
        "__REVIEW_GOOGLE_RATING__":      _review_rating(reviews, "google"),
        "__REVIEW_GOOGLE_COUNT__":       _review_count(reviews, "google"),
        "__REVIEW_BOOKING_RATING__":     _review_rating(reviews, "booking"),
        "__REVIEW_BOOKING_COUNT__":      _review_count(reviews, "booking"),
        "__REVIEW_HOSTELWORLD_RATING__": _review_rating(reviews, "hostelworld"),
        "__REVIEW_HOSTELWORLD_COUNT__":  _review_count(reviews, "hostelworld"),
        "__REVIEW_EXPEDIA_RATING__":     _review_rating(reviews, "expedia"),
        "__REVIEW_EXPEDIA_COUNT__":      _review_count(reviews, "expedia"),
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
        "__ROOM_ADR_CHART_LABELS__":   json.dumps(room_adr_labels),
        "__ROOM_ADR_PRIVATE_DATA__":   json.dumps(room_adr_private_data),
        "__ROOM_ADR_PODS_DATA__":      json.dumps(room_adr_pods_data),
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
