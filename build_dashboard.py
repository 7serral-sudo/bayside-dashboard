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

# Full-year revenue goal. Progress is shown against how far through the year we
# actually are, because 60% of the goal is ahead in June and behind in October
# -- the bar carries a pace marker so the two are compared for you.
REVENUE_GOAL = 900_000.0

# Per-room-type bed counts change as rooms are reconfigured during the year,
# and neither Cloudbeds nor the sheet records that history -- ROOM_TYPES carries
# only TODAY's count. Dividing a full year of nights by today's beds is what
# made the 4 Bed Dorm read 109.1% (physically impossible) while the Female Dorm
# read 54%, understating it by about 20 points.
#
# Each entry is (date the count took effect, beds from that date), earliest
# first. Days before a type's first entry count as zero capacity, which is what
# a room that entered service mid-year needs. Types absent from this table were
# not reconfigured and use their ROOM_TYPES count throughout.
#
# Confirmed by the property, and corroborated by nights sold -- any month whose
# nights exceed beds x days proves the count must have been higher then:
#   4 Bed Dorm   1 room in Jan, 3 rooms Feb-May, 2 from Jun (one became a female dorm)
#   Female Dorm  4 beds per room: 1 room to Feb, 2 from Mar, 3 from May
#   Private (Grd Floor)  entered service mid-July -- 22 nights sold across
#                        exactly its 22 days in service, which is what fixes the date
#
# Month boundaries are approximate: the real changes happened mid-month, leaving
# a ~3% residual in March (12 nights). Once a year rolls over, the last entry
# for each type is carried forward, and every one of those equals its ROOM_TYPES
# count -- so this degrades to today's behaviour rather than going stale.
ROOM_TYPE_BED_HISTORY = {
    # 4 Bed Dorm
    "514747": [(date(2026, 1, 1), 4), (date(2026, 2, 1), 12), (date(2026, 6, 1), 8)],
    # Female Dorm
    "462944": [(date(2026, 1, 1), 4), (date(2026, 3, 1), 8), (date(2026, 5, 1), 12)],
    # Private (Grd Floor)
    "679030": [(date(2026, 7, 14), 1)],
}

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
        types.append({"id": rt_id, "name": name, "beds": beds, "section": section,
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


def _fetch_website_analytics_from_sheet(service, sheet_id):
    """Fallback used when a live GA4 query isn't available. Aggregates every
    week's GA4 row that falls within the most recent week's calendar month,
    so the dashboard shows monthly totals rather than a single week's
    (smaller, noisier) numbers. Sessions/pageviews sum cleanly; "Users"
    becomes a sum-of-weekly-users approximation since GA4 weekly snapshots
    can't be de-duplicated into a true unique-monthly count after the fact.

    Because the underlying rows are captured Tuesday-to-Tuesday rather than
    on calendar-month boundaries, the range this ends up covering can
    straddle into the previous month (e.g. "29 Jul - 11 Aug" for "August")."""
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

    # The actual date range the summed weekly rows cover, so the section
    # title can say "1-11 Aug 2026" instead of a week count that reads as
    # ambiguous (does "2 weeks" mean the two most recent weeks, a rolling
    # 14-day window, or just week 2?). Each row is a 7-day GA4 snapshot
    # ending on its listed date, so the range starts 6 days before the
    # earliest included row.
    date_range = None
    if month_rows:
        first_end = _date_str(month_rows[0][0])
        try:
            start_d = datetime.strptime(first_end, "%d/%m/%Y").date() - timedelta(days=6)
            end_d = datetime.strptime(last_date, "%d/%m/%Y").date()
            if start_d.year == end_d.year and start_d.month == end_d.month:
                date_range = f"{start_d.day}–{end_d.day} {end_d.strftime('%b')} {end_d.year}"
            elif start_d.year == end_d.year:
                date_range = f"{start_d.day} {start_d.strftime('%b')} – {end_d.day} {end_d.strftime('%b')} {end_d.year}"
            else:
                date_range = f"{fmt_date_human(start_d.strftime('%d/%m/%Y'))} – {fmt_date_human(last_date)}"
        except ValueError:
            date_range = None

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
        "date_range": date_range,
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


def _fetch_website_analytics_live(week_end_date: date, current_year: int) -> dict:
    """Live GA4 query for the 1st of the month through week_end_date, compared
    against the identical calendar dates a year earlier -- an exact date-for-
    date comparison, unlike the Sheet-based fallback (which sums whichever
    weekly rows land in the month and can straddle into the previous one,
    since those rows are captured Tuesday-to-Tuesday, not on calendar-month
    boundaries)."""
    from ga4_client import GA4Client, GA4_CHANNELS

    ga4 = GA4Client()
    month_start = date(current_year, week_end_date.month, 1)
    ly_start = date(current_year - 1, week_end_date.month, 1)
    ly_end = date(current_year - 1, week_end_date.month, week_end_date.day)

    traffic = ga4.get_weekly_traffic(month_start, week_end_date)
    ly_traffic = ga4.get_weekly_traffic(ly_start, ly_end)
    demo = ga4.get_demographics(month_start, week_end_date)
    ly_demo = ga4.get_demographics(ly_start, ly_end)

    def _flatten_channels(traffic_result):
        # Unrecognised channels are normalised to "Other" upstream in
        # get_weekly_traffic(); fold that into "Unassigned" so the channel
        # set matches WEB_CHANNELS exactly, same as the Sheet-based path.
        out = {ch: 0 for ch in GA4_CHANNELS}
        for ch, vals in traffic_result["channels"].items():
            out[ch if ch in out else "Unassigned"] += vals["sessions"]
        return out

    devices = {dv: demo["devices"].get(dv.lower(), 0) for dv in sheets_client.DEVICES}
    ly_devices = {dv: ly_demo["devices"].get(dv.lower(), 0) for dv in sheets_client.DEVICES}

    if month_start.month == week_end_date.month:
        date_range = f"{month_start.day}–{week_end_date.day} {week_end_date.strftime('%b')} {week_end_date.year}"
    else:
        date_range = f"{fmt_date_human(month_start.strftime('%d/%m/%Y'))} – {fmt_date_human(week_end_date.strftime('%d/%m/%Y'))}"
    ly_label = f"{ly_start.day}–{ly_end.day} {ly_end.strftime('%b')} {ly_end.year}"

    return {
        "month_label":      f"{sheets_client.MONTHS[week_end_date.month - 1]} {current_year}",
        "date_range":       date_range,
        "sessions":         traffic["total_sessions"],
        "users":            traffic["total_users"],
        "pageviews":        traffic["total_pageviews"],
        "channels":         _flatten_channels(traffic),
        "countries":        demo["top_countries"],
        "devices":          devices,
        "prev_month_label": ly_label,
        "prev_sessions":    ly_traffic["total_sessions"],
        "prev_users":       ly_traffic["total_users"],
        "prev_pageviews":   ly_traffic["total_pageviews"],
        "ly_channels":      _flatten_channels(ly_traffic),
        "ly_countries":     dict(ly_demo["top_countries"]),
        "ly_devices":       ly_devices,
    }


def fetch_website_analytics(service, sheet_id, week_end_date: date, current_year: int, log=print) -> dict | None:
    """Prefers a live, exact-date-matched GA4 query; falls back to summing
    the Website Analytics sheet tab (e.g. if GA4 credentials aren't
    available in this environment) so a build never hard-fails over it."""
    try:
        return _fetch_website_analytics_live(week_end_date, current_year)
    except Exception as e:
        log(f"  !! Live GA4 fetch failed ({e}) -- falling back to the Website Analytics sheet tab.")
        return _fetch_website_analytics_from_sheet(service, sheet_id)


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


def build_monthly_cards_html(occ_monthly: dict, revenue: dict, current_year: int,
                              ref_2025: dict = None, week_end_date: date = None):
    cards = []
    for m in sheets_client.MONTHS:
        if m not in occ_monthly:
            continue
        occ_pct = occ_monthly[m]
        rev = revenue.get(m, {}).get("cy", 0)
        month_num = sheets_client.MONTHS.index(m) + 1
        is_current_month = week_end_date is not None and month_num == week_end_date.month

        # YoY comparisons, day-matched against the 2025 reference data. A
        # month still in progress only has partial current-year figures, so
        # the prior-year side must be cut off at the same day-of-month --
        # otherwise a handful of days this year gets compared against all of
        # last year's month (the same bug the "This month" KPI card had: Aug
        # MTD vs a full prior Aug made occupancy and revenue read backwards).
        # This also replaces the old comparison, which quietly used the
        # month's check-in *count* change while labelling it "occ".
        yoy_text = ""
        if ref_2025:
            ly_month_start = date(current_year - 1, month_num, 1)
            if is_current_month:
                ly_month_end = date(current_year - 1, month_num, week_end_date.day)
            elif month_num < 12:
                ly_month_end = date(current_year - 1, month_num + 1, 1) - timedelta(days=1)
            else:
                ly_month_end = date(current_year - 1, 12, 31)
            ly_booked, ly_rev = ly_range_sums(ref_2025, ly_month_start, ly_month_end)
            ly_days = (ly_month_end - ly_month_start).days + 1

            if ly_booked:
                ly_occ = ly_booked / (LY_N_BEDS * ly_days) * 100
                occ_pct_change = ((occ_pct - ly_occ) / ly_occ * 100)
                occ_sign = "+" if occ_pct_change >= 0 else ""
                occ_color = '#3FCF6E' if occ_pct_change >= 0 else '#F0564A'
                occ_part = f'<span style="color:{occ_color}">{occ_sign}{occ_pct_change:.0f}% occ</span> · '
            else:
                occ_part = ""

            if ly_rev:
                rev_pct = ((rev - ly_rev) / ly_rev * 100)
                rev_sign = "+" if rev_pct >= 0 else ""
                rev_color = '#3FCF6E' if rev_pct >= 0 else '#F0564A'
            else:
                rev_pct, rev_sign, rev_color = 0, "", "#888888"

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


def _beds_on(rt_id, day, current_beds):
    """Beds a room type had on a given date, honouring any reconfigurations.

    Falls back to today's count for types that were never reconfigured. For a
    type that WAS, days before its first entry return zero -- a room that opened
    in July must not have the first half of the year counted against it.
    """
    history = ROOM_TYPE_BED_HISTORY.get(rt_id)
    if not history:
        return current_beds
    beds = 0
    for effective, count in history:
        if day < effective:
            break
        beds = count
    return beds


def _add_room_type_occupancy(types, week_end_date, current_year):
    """Adds a YTD occupancy % per room type: nights sold / bed-nights available.

    Capacity is accumulated day by day rather than as beds x days_elapsed,
    because several room types were reconfigured mid-year and today's bed count
    does not describe January (see ROOM_TYPE_BED_HISTORY). Mutates in place.
    """
    start = date(current_year, 1, 1)
    n_days = (week_end_date - start).days + 1
    for t in types:
        avail = sum(_beds_on(t["id"], start + timedelta(days=i), t["beds"])
                    for i in range(n_days))
        t["avail"] = avail
        t["occ"] = (t["nights"] / avail * 100) if avail else 0.0


# Occupancy targets differ by section: a private room has to work harder to pay
# for itself than a dorm bed does. At or above target reads green, below it red.
# Keyed by the same "section" value the room types already carry.
OCC_TARGET = {"private": 80.0, "dorm": 70.0}
OCC_TARGET_DEFAULT = 70.0


def _occ_class(occ, section):
    target = OCC_TARGET.get(section, OCC_TARGET_DEFAULT)
    return "occ-strong" if occ >= target else "occ-weak"


def _occ_span(occ, section):
    """Coloured percentage for use inline, where the rest of the line is not
    part of the metric (the hero stats sit on a shared comparison line)."""
    return f'<span class="{_occ_class(occ, section)}">{occ:.1f}%</span>'


def _section_occupancy(types, section):
    """Occupancy for a whole section (private rooms or pods).

    Summed from the same per-type nights and day-by-day capacity the individual
    tiles use, so the headline card and the breakdown beneath it cannot
    disagree. Returns None when the section has no capacity to divide by.
    """
    members = [t for t in types if t["section"] == section]
    nights = sum(t["nights"] for t in members)
    avail = sum(t.get("avail", 0) for t in members)
    return (nights / avail * 100) if avail else None


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
            <div class="room-occ num {_occ_class(t.get("occ", 0), t["section"])}">{t.get("occ", 0):.1f}% occupancy</div>
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


def _parse_week_date(s):
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def _prior_week(weeks, log, tab_name):
    """The week to compare the latest one against, preferring a full seven days
    back.

    Taking weeks[-2] blindly breaks two ways, and both are silent rather than
    loud. A DUPLICATED row makes the week compare against itself, and because
    _cmp_html treats equal values as "no change" it renders as a flat
    sub-label with no arrow -- this shipped, occupancy reading "vs last week:
    67.0%" against its own 67.0% while the real prior week was 73.6%. An
    OFF-CYCLE row does subtler damage: an extra run on 29 Jul 2026, a day
    after the 28 Jul one, left the 4 Aug week measured across a 6-day gap
    instead of a clean week.

    So: drop rows repeating the latest date, then take the row exactly seven
    days back if one exists, else the most recent earlier row. Anything skipped
    is logged so the weekly run surfaces it, and None (rendering "n/a") is
    returned rather than inventing a comparison. On a clean sheet this picks
    weeks[-2], exactly as before.
    """
    if len(weeks) < 2:
        return None

    latest_date = weeks[-1]["date"]
    earlier = [w for w in weeks[:-1] if w["date"] != latest_date]
    dupes = len(weeks) - 1 - len(earlier)
    if dupes:
        log(f"  !! {tab_name}: {dupes} duplicate row(s) for {latest_date} -- "
            f"excluded from the week-on-week comparison.")
    if not earlier:
        return None

    latest = _parse_week_date(latest_date)
    if latest:
        target = latest - timedelta(days=7)
        exact = [w for w in earlier if _parse_week_date(w["date"]) == target]
        if exact:
            chosen = exact[-1]
            if chosen is not earlier[-1]:
                log(f"  !! {tab_name}: {earlier[-1]['date']} is off-cycle "
                    f"({(latest - _parse_week_date(earlier[-1]['date'])).days}d before "
                    f"{latest_date}); comparing against {chosen['date']} instead.")
            return chosen

    return earlier[-1]


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
    log("  -> Reading Room Type ADR tab ...")
    room_type_adr = fetch_room_type_adr(service, sheet_id)

    if not occ_weeks or not perf_weeks:
        raise RuntimeError("No data found in Occupancy/Performance tabs -- has weekly_report.py run yet?")

    latest_occ  = occ_weeks[-1]
    latest_perf = perf_weeks[-1]
    week_end_str = latest_occ["date"]
    week_end_date = datetime.strptime(week_end_str, "%d/%m/%Y").date()
    current_year = int(week_end_str.split("/")[-1])

    log("  -> Fetching Website Analytics (live GA4) ...")
    web = fetch_website_analytics(service, sheet_id, week_end_date, current_year, log=log)

    if room_type_adr and room_type_adr.get("types"):
        _add_room_type_occupancy(room_type_adr["types"], week_end_date, current_year)

    occ_monthly = {w["month"]: w["month_occ"] for w in occ_weeks if w["month"] and w["month_occ"] is not None}
    ref_2025 = load_2025_reference()

    # -- This Week KPIs (vs last week) ---------------------------------------
    prev_occ  = _prior_week(occ_weeks, log, sheets_client.OCC_TAB)
    prev_perf = _prior_week(perf_weeks, log, sheets_client.PERF_TAB)

    # Name the weeks being compared rather than claiming "the one before". The
    # weekly job can run off-cycle (28 Jul and again on 29 Jul 2026), so
    # consecutive rows are not always seven days apart, and the reader needs to
    # see which two dates the comparison actually used.
    if prev_occ:
        week_range_note = (f'Week ending {fmt_date_human(week_end_str)} '
                           f'vs week ending {fmt_date_human(prev_occ["date"])}')
    else:
        week_range_note = f'Week ending {fmt_date_human(week_end_str)}'

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
    # State the days covered. "This month (Aug)" beside a $6.9k revenue figure
    # reads as a whole month when it is really the first four days of one.
    mtd_range_note = (f'1–{week_end_date.day} {current_month_abbr} {current_year} '
                      f'vs the same days last year')
    occ_month_val = occ_monthly.get(current_month_abbr)
    occ_month_pct = f'{occ_month_val:.1f}%' if occ_month_val is not None else 'n/a'
    month_idx = sheets_client.MONTHS.index(current_month_abbr)
    prev_month_abbr = sheets_client.MONTHS[month_idx - 1] if month_idx > 0 else None
    prev_month_occ = occ_monthly.get(prev_month_abbr) if prev_month_abbr else None
    occ_lastmonth_pct = f'{prev_month_occ:.1f}%' if prev_month_occ is not None else 'n/a'
    adr_month = fmt_money(latest_perf["adr_mtd"])

    # Compare with same month last year (from 2025 reference data). The month
    # in progress is only ever partial, so the prior-year side must be cut off
    # at the same day-of-month -- comparing against the *complete* prior-year
    # month (as this used to) understates last year's pace and made this
    # year's occupancy/ADR/revenue deltas all read wrong (e.g. Aug 2026 MTD
    # vs the full Aug 2025 total showed occupancy up and revenue down at once).
    occ_month_ly = 'n/a'
    adr_month_ly = 'n/a'
    ly_mtd_booked, ly_mtd_rev = None, None
    if ref_2025:
        ly_month_start = date(current_year - 1, week_end_date.month, 1)
        ly_month_end = date(current_year - 1, week_end_date.month, week_end_date.day)
        ly_mtd_booked, ly_mtd_rev = ly_range_sums(ref_2025, ly_month_start, ly_month_end)
        ly_days = (ly_month_end - ly_month_start).days + 1
        if ly_days and ly_mtd_booked:
            ly_occ_month = ly_mtd_booked / (LY_N_BEDS * ly_days) * 100
            ly_adr_month = ly_mtd_rev / ly_mtd_booked
            occ_month_ly = _cmp_html('vs last year', occ_month_val, ly_occ_month,
                                      fmt_fn=lambda v: f'{v:.1f}%')
            adr_month_ly = _cmp_html('vs last year', latest_perf["adr_mtd"], ly_adr_month,
                                      fmt_fn=fmt_money)

    # -- Revenue ----------------------------------------------------------
    ytd_revenue = sum(v["cy"] for v in revenue.values())
    mtd = revenue.get(current_month_abbr, {"py": 0, "cy": 0})
    mtd_revenue = mtd["cy"]
    # Prefer the day-matched ref_2025 sum over the sheet's "py" column: the
    # sheet stores the complete prior-year month, which only matches once the
    # current month has also finished.
    mtd_ly = ly_mtd_rev if ly_mtd_rev else mtd["py"]
    if mtd_ly:
        yoy_pct = (mtd_revenue - mtd_ly) / mtd_ly * 100
        yoy_sign = "+" if yoy_pct >= 0 else ""
        mtd_yoy_label = _color_yoy(f'vs {current_month_abbr} {current_year - 1}: {fmt_money_k(mtd_ly)} ({yoy_sign}{yoy_pct:.0f}%)')
    else:
        mtd_yoy_label = f'vs {current_month_abbr} {current_year - 1}: n/a'

    # Calendar week of the year, NOT the row count. len(occ_weeks) counted rows,
    # which silently inflates whenever the weekly job runs off-cycle: an extra
    # run on 29 Jul 2026 put 32 rows in a year that had only elapsed 31 weeks,
    # and "Week 32 of 52" happened to look right purely by coincidence.
    iso_year, week_number, _ = week_end_date.isocalendar()
    # 28 December always falls in the final ISO week, so this gives 52 or 53.
    weeks_in_year = date(iso_year, 12, 28).isocalendar()[1]

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
        _private_occ = _section_occupancy(room_type_adr.get("types", []), "private")
        _pods_occ = _section_occupancy(room_type_adr.get("types", []), "dorm")
        private_occ = _occ_span(_private_occ, "private") if _private_occ is not None else 'n/a'
        pods_occ = _occ_span(_pods_occ, "dorm") if _pods_occ is not None else 'n/a'
        # Build chart data for room type ADR trends
        room_adr_monthly = room_type_adr.get("monthly", {})
        room_adr_labels = room_adr_monthly.get("months", [])
        room_adr_private_data = [round(v, 2) for v in room_adr_monthly.get("private_adr", [])]
        room_adr_pods_data = [round(v, 2) for v in room_adr_monthly.get("pods_adr", [])]
    else:
        private_adr = 'n/a'
        pods_adr = 'n/a'
        private_occ = 'n/a'
        pods_occ = 'n/a'
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
        # "Website Analytics (Aug 2026)" claimed a whole month when only one
        # week of it had been collected. These are GA4 weekly rows grouped by
        # the month their week ENDS in, so the honest label is how many weeks
        # are in hand and the date they run to -- which also matches the
        # "vs same N wks of ..." comparisons beneath it.
        web_title = f'Website Analytics ({web["date_range"] or web["month_label"]})'
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

    # -- Revenue goal -------------------------------------------------------
    # Compared against elapsed time, not just the raw total: hitting 60% of the
    # goal is ahead of pace in June and behind it in October.
    goal_pct = (ytd_revenue / REVENUE_GOAL * 100) if REVENUE_GOAL else 0
    days_in_year = (date(current_year, 12, 31) - date(current_year, 1, 1)).days + 1
    year_pct = ((week_end_date - date(current_year, 1, 1)).days + 1) / days_in_year * 100
    goal_remaining = max(REVENUE_GOAL - ytd_revenue, 0)
    ahead = goal_pct - year_pct

    # A round goal reads better without the trailing .0 that fmt_money_k adds.
    goal_str = (f"${REVENUE_GOAL / 1000:,.0f}k" if REVENUE_GOAL % 1000 == 0
                else fmt_money_k(REVENUE_GOAL))
    goal_label = f"{current_year} revenue goal · {goal_str}"
    goal_pct_str = f"{goal_pct:.0f}%"
    goal_width = f"{min(goal_pct, 100):.1f}%"
    goal_pace_left = f"{min(year_pct, 100):.1f}%"
    pace_word = "ahead of" if ahead >= 0 else "behind"
    pace_colour = "#3FCF6E" if ahead >= 0 else "#F0564A"
    pts = abs(ahead)
    pts_str = f"{pts:.0f} point{'' if round(pts) == 1 else 's'}"
    goal_sub = (f"<strong>{fmt_money_k(ytd_revenue)}</strong> banked · "
                f"{fmt_money_k(goal_remaining)} to go · "
                f"{year_pct:.0f}% of the year gone, so "
                f"<span style=\"color:{pace_colour}\">{pts_str} "
                f"{pace_word} pace</span>")
    goal_aria = (f"{goal_pct:.0f} percent of the {goal_str} revenue goal, "
                 f"with {year_pct:.0f} percent of the year elapsed")

    # -- Build HTML blocks -------------------------------------------------
    monthly_cards_html = build_monthly_cards_html(occ_monthly, revenue, current_year, ref_2025, week_end_date)

    footer_text = (f"Bayside House Dashboard · Week {week_number} of {weeks_in_year} · "
                   f"Year-to-date data through {fmt_date_human(week_end_str)}")

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
        "__PRIVATE_ROOM_OCC_YTD__": private_occ,
        "__PODS_OCC_YTD__":        pods_occ,
        "__ROOM_TYPE_CARDS_HTML__": room_type_cards_html,
        "__GOAL_LABEL__":          goal_label,
        "__GOAL_PCT__":            goal_pct_str,
        "__GOAL_WIDTH__":          goal_width,
        "__GOAL_PACE_LEFT__":      goal_pace_left,
        "__GOAL_SUB__":            goal_sub,
        "__GOAL_ARIA__":           goal_aria,
        "__YTD_REVENUE__":         fmt_money_k(ytd_revenue),
        "__YTD_REVENUE_WEEK_LABEL__": ytd_revenue_label,
        "__MTD_REVENUE__":         fmt_money_k(mtd_revenue),
        "__MTD_MONTH_LABEL__":     f"({current_month_abbr})",
        "__WEEK_RANGE_NOTE__":     week_range_note,
        "__MTD_RANGE_NOTE__":      mtd_range_note,
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
