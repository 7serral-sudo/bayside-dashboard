"""
Google Sheets client — writes weekly report to Performance, Occupancy, Revenue tabs.
Uses a service account JSON key (no OAuth browser flow needed).
"""
import os
from datetime import date

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    SHEETS_AVAILABLE = True
except ImportError:
    SHEETS_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

MONTHS  = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SOURCES = ["Booking.com", "Expedia", "HostelWorld", "Agoda", "Website", "Walk-In", "Phone"]

PERF_TAB = "Performance"
OCC_TAB  = "Occupancy"
REV_TAB  = "Revenue"
REV_TAB2 = "Reviews"
WEB_TAB  = "Website Analytics"
PLAT_TAB = "Platform Reviews"

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _rgb(hex_str: str) -> dict:
    h = hex_str.lstrip("#")
    return {"red": int(h[0:2], 16) / 255,
            "green": int(h[2:4], 16) / 255,
            "blue":  int(h[4:6], 16) / 255}

WHITE      = _rgb("FFFFFF")
BLACK      = _rgb("000000")
RED        = _rgb("FF0000")
BLUE_DARK  = _rgb("2F5496")   # check-in header
BLUE_LIGHT = _rgb("BDD7EE")   # check-in sub-header
GREEN_DARK = _rgb("375623")   # date-booked header
GREEN_LIGHT= _rgb("E2EFDA")   # date-booked sub-header
ORANGE_D   = _rgb("833C0B")   # metrics header
ORANGE_L   = _rgb("FCE4D6")   # metrics sub-header
GREY_DARK  = _rgb("404040")   # week-ended header
GREY_LIGHT = _rgb("F2F2F2")   # alternating row
PURPLE_D   = _rgb("4B3070")   # occupancy header
PURPLE_L   = _rgb("E8D5F5")
TEAL_D     = _rgb("1F5C5C")
TEAL_L     = _rgb("D5EFEF")

# ---------------------------------------------------------------------------
# Batch-update building blocks
# ---------------------------------------------------------------------------

def _rng(sid, r0, r1, c0, c1):
    return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def _fmt(sid, r0, r1, c0, c1, bg=None, bold=False, fg=WHITE,
         halign=None, valign=None, num_fmt=None, wrap=None):
    fmt = {}
    if bg:
        fmt["backgroundColor"] = bg
    tf = {}
    if bold:
        tf["bold"] = True
    if fg:
        tf["foregroundColor"] = fg
    if tf:
        fmt["textFormat"] = tf
    if halign:
        fmt["horizontalAlignment"] = halign
    if valign:
        fmt["verticalAlignment"] = valign
    if num_fmt:
        fmt["numberFormat"] = num_fmt
    if wrap:
        fmt["wrapStrategy"] = wrap
    fields_parts = []
    if bg:           fields_parts.append("backgroundColor")
    if tf:           fields_parts.append("textFormat")
    if halign:       fields_parts.append("horizontalAlignment")
    if valign:       fields_parts.append("verticalAlignment")
    if num_fmt:      fields_parts.append("numberFormat")
    if wrap:         fields_parts.append("wrapStrategy")
    return {"repeatCell": {
        "range": _rng(sid, r0, r1, c0, c1),
        "cell": {"userEnteredFormat": fmt},
        "fields": "userEnteredFormat(" + ",".join(fields_parts) + ")",
    }}


def _merge(sid, r0, c0, c1):
    return {"mergeCells": {
        "range": _rng(sid, r0, r0 + 1, c0, c1),
        "mergeType": "MERGE_ALL",
    }}


def _freeze(sid, rows=0, cols=0):
    return {"updateSheetProperties": {
        "properties": {"sheetId": sid,
                       "gridProperties": {"frozenRowCount": rows,
                                          "frozenColumnCount": cols}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
    }}


def _col_width(sid, c0, c1, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS",
                  "startIndex": c0, "endIndex": c1},
        "properties": {"pixelSize": px},
        "fields": "pixelSize",
    }}


def _border_range(sid, r0, r1, c0, c1, style="SOLID", width=1):
    b = {"style": style, "width": width, "color": _rgb("AAAAAA")}
    return {"updateBorders": {
        "range": _rng(sid, r0, r1, c0, c1),
        "top": b, "bottom": b, "left": b, "right": b,
        "innerHorizontal": b, "innerVertical": b,
    }}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def _build_service():
    if not SHEETS_AVAILABLE:
        raise RuntimeError(
            "Google API libraries not installed. "
            "Run: pip install google-auth google-auth-httplib2 google-api-python-client"
        )
    key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
    creds = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _get_tabs(service, sheet_id: str) -> dict[str, int]:
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"]
            for s in meta["sheets"]}


def _ensure_tab(service, sheet_id: str, tab_name: str, existing: dict):
    if tab_name not in existing:
        resp = service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        new_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        existing[tab_name] = new_id
        print(f"  Created tab: {tab_name}")
    return existing[tab_name]


def _clear_tab(service, sheet_id: str, tab_name: str):
    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id,
        range=f"{tab_name}!A1:ZZ",
    ).execute()


def _batch(service, sheet_id: str, requests: list):
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": requests},
        ).execute()


# ---------------------------------------------------------------------------
# Performance tab
#
# Layout matches user's manual spreadsheet:
#   Col A  : Week ended
#   Cols B-M  : Check In — 6 sources × 2 sub-cols (CI black, C red)
#   Col N  : blank
#   Col O  : CI Total
#   Col P  : Total Beds (guests in house at week-end)
#   Col Q  : C total (sum of all CI cancellation sub-cols)
#   Col R  : blank separator
#   Cols S-AD : Date Booked — 6 sources × 2 sub-cols (CI black, C red)
#   Col AE : blank
#   Col AF : DB Total
#   Col AG : DB Total Beds
# ---------------------------------------------------------------------------

DISPLAY_SOURCES = ["Booking.com", "Expedia", "HW", "Agoda", "Website", "Walk + Ph"]
N_DSRC = 6

# 0-indexed column indices
CI_SRC_START = 1    # B  (CI Booking)
CI_TOTAL_COL = 13   # N  Total check-ins
CI_BEDS_COL  = 14   # O  Total Beds (people in house)
CI_CTOT_COL  = 15   # P  C (cancellation total)
SEP_COL      = 16   # Q  blank separator
DB_SRC_START = 17   # R  (DB Booking)
DB_TOTAL_COL = 29   # AD  Total bookings created
DB_LASTMIN_COL = 30 # AE  bookings made AND checked in during the same week
DB_BEDS_COL  = 31   # AF  blank (no beds metric for DB)
DB_CTOT_COL  = 32   # AG  DB cancellations total
MET_SEP_COL  = 33   # AH  blank separator
ADR_COL      = 34   # AI  ADR (weekly)
ADR_YTD_COL  = 35   # AJ  YTD ADR
ADR_MTD_COL  = 36   # AK  MTD ADR
LONG_TERMERS_COL = 37  # AL  Long-termers (>28 nights) in house at week end
PERF_TOTAL_COLS = 38

# Header row values
_PERF_HDR1 = (
    [""] + ["Check In"] + [None] * 14 + [""] + ["Date Booked"] + [None] * 14 +
    ["", "Key Metrics", None, None, None, None]
)
_PERF_HDR2 = (
    ["Week ended"] +
    [v for src in DISPLAY_SOURCES for v in (src, None)] +
    ["Total", "Total Beds", "C"] +
    [""] +
    [v for src in DISPLAY_SOURCES for v in (src, None)] +
    ["Total", "Last-Min BK", "Total Beds", "C"] +
    ["", "ADR ($)", "YTD ADR ($)", "MTD ADR ($)", "Long-Termers (28+)"]
)


def _setup_performance(service, sheet_id: str, sid: int):
    # Unmerge, wipe all formatting, then clear values
    _batch(service, sheet_id, [
        {"unmergeCells": {"range": _rng(sid, 0, 5, 0, 70)}},
        {"repeatCell": {          # reset ALL formatting so no stale currency/colours remain
            "range": _rng(sid, 0, 1000, 0, 70),
            "cell": {"userEnteredFormat": {}},
            "fields": "userEnteredFormat",
        }},
    ])
    _clear_tab(service, sheet_id, PERF_TAB)

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{PERF_TAB}!A1",
        valueInputOption="RAW",
        body={"values": [_PERF_HDR1, _PERF_HDR2]},
    ).execute()

    # Merge section headers (row 1) and source names (row 2)
    merges = [
        _merge(sid, 0, CI_SRC_START, SEP_COL),          # "Check In"
        _merge(sid, 0, DB_SRC_START, MET_SEP_COL),       # "Date Booked"
        _merge(sid, 0, MET_SEP_COL + 1, PERF_TOTAL_COLS),# "Key Metrics"
    ]
    for i in range(N_DSRC):
        c  = CI_SRC_START + i * 2
        c2 = DB_SRC_START + i * 2
        merges += [_merge(sid, 1, c, c + 2), _merge(sid, 1, c2, c2 + 2)]

    # Pre-format cancellation sub-columns red (rows 3 onward)
    red_fmt = []
    for i in range(N_DSRC):
        c  = CI_SRC_START + i * 2 + 1
        c2 = DB_SRC_START + i * 2 + 1
        red_fmt += [
            _fmt(sid, 2, 1000, c,  c  + 1, fg=RED),
            _fmt(sid, 2, 1000, c2, c2 + 1, fg=RED),
        ]

    reqs = [
        _freeze(sid, rows=2, cols=1),
        # Row 1 colours
        _fmt(sid, 0, 1, 0,               1,               bg=GREY_DARK,  bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, CI_SRC_START,    SEP_COL,         bg=BLUE_DARK,  bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, DB_SRC_START,    MET_SEP_COL,     bg=GREEN_DARK, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, MET_SEP_COL + 1, PERF_TOTAL_COLS, bg=ORANGE_D,   bold=True, fg=WHITE, halign="CENTER"),
        # Row 2 colours
        _fmt(sid, 1, 2, 0,               1,               bg=GREY_DARK,   bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 1, 2, CI_SRC_START,    SEP_COL,         bg=BLUE_LIGHT,  bold=True, fg=BLACK, halign="CENTER", wrap="WRAP"),
        _fmt(sid, 1, 2, DB_SRC_START,    MET_SEP_COL,     bg=GREEN_LIGHT, bold=True, fg=BLACK, halign="CENTER", wrap="WRAP"),
        _fmt(sid, 1, 2, MET_SEP_COL + 1, PERF_TOTAL_COLS, bg=ORANGE_L,    bold=True, fg=BLACK, halign="CENTER", wrap="WRAP"),
        # ADR currency format (data rows) -- weekly/YTD/MTD ADR are currency, Long-Termers is a plain count
        _fmt(sid, 2, 1000, ADR_COL, ADR_MTD_COL + 1,
             num_fmt={"type": "NUMBER", "pattern": '"$"#,##0.00'}),
        _fmt(sid, 2, 1000, LONG_TERMERS_COL, PERF_TOTAL_COLS, halign="CENTER"),
        # Column widths
        _col_width(sid, 0,            1,            90),   # Week ended
        *[_col_width(sid, CI_SRC_START + i*2,     CI_SRC_START + i*2 + 1, 55) for i in range(N_DSRC)],
        *[_col_width(sid, CI_SRC_START + i*2 + 1, CI_SRC_START + i*2 + 2, 35) for i in range(N_DSRC)],
        _col_width(sid, CI_BLANK_COL, CI_BLANK_COL + 1, 15),
        _col_width(sid, CI_TOTAL_COL, CI_TOTAL_COL + 1, 55),
        _col_width(sid, CI_BEDS_COL,  CI_BEDS_COL  + 1, 75),
        _col_width(sid, CI_CTOT_COL,  CI_CTOT_COL  + 1, 40),
        _col_width(sid, SEP_COL,      SEP_COL      + 1, 15),
        *[_col_width(sid, DB_SRC_START + i*2,     DB_SRC_START + i*2 + 1, 55) for i in range(N_DSRC)],
        *[_col_width(sid, DB_SRC_START + i*2 + 1, DB_SRC_START + i*2 + 2, 35) for i in range(N_DSRC)],
        _col_width(sid, DB_BLANK_COL,    DB_BLANK_COL + 1,    15),
        _col_width(sid, DB_TOTAL_COL,    DB_TOTAL_COL + 1,    55),
        _col_width(sid, DB_LASTMIN_COL,  DB_LASTMIN_COL + 1,  95),
        _col_width(sid, DB_BEDS_COL,     DB_BEDS_COL  + 1,    75),
        _col_width(sid, MET_SEP_COL,     MET_SEP_COL  + 1,    15),
        _col_width(sid, ADR_COL,         ADR_MTD_COL  + 1,    90),
        _col_width(sid, LONG_TERMERS_COL, PERF_TOTAL_COLS,    120),
        *merges,
        *red_fmt,
    ]
    _batch(service, sheet_id, reqs)


def _dsrc_val(src_dict: dict, display_src: str) -> int:
    """Map a DISPLAY_SOURCE label to its count from raw API source dict."""
    if display_src == "Walk + Ph":
        return src_dict.get("Walk-In", 0) + src_dict.get("Phone", 0)
    if display_src == "HW":
        return src_dict.get("HostelWorld", 0)
    return src_dict.get(display_src, 0)


def _existing_week_dates(service, sheet_id: str) -> set[str]:
    """Return the set of week-end dates already written to the Performance tab (DD/MM/YYYY)."""
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{PERF_TAB}!A:A",
    ).execute()
    dates = set()
    for row in result.get("values", [])[2:]:   # skip 2 header rows
        if row and row[0]:
            dates.add(row[0])
    return dates


def _next_perf_row(service, sheet_id: str) -> int:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{PERF_TAB}!A:A",
    ).execute()
    return len(result.get("values", [])) + 1   # 1-based


def append_performance(service, sheet_id: str, sid: int, s: dict, week_end: date):
    """Append one week's CI + Date Booked data to the Performance tab."""
    ci   = s["ci"]
    ci_c = s.get("ci_cancel", {})
    db   = s.get("db", {})
    db_c = s.get("db_cancel", {})

    # Check In pairs (confirmed black + cancellation red per source)
    ci_pairs, c_sum = [], 0
    for dsrc in DISPLAY_SOURCES:
        v = _dsrc_val(ci,   dsrc)
        c = _dsrc_val(ci_c, dsrc)
        ci_pairs += [v, c]
        c_sum    += c
    ci_total = sum(ci_pairs)  # confirmed + cancelled

    # Date Booked pairs (confirmed black + cancellation red per source)
    db_pairs, db_c_sum = [], 0
    for dsrc in DISPLAY_SOURCES:
        v = _dsrc_val(db,   dsrc)
        c = _dsrc_val(db_c, dsrc)
        db_pairs += [v, c]
        db_c_sum += c
    # DB Total = all bookings created this week (confirmed + cancelled), matches Cloudbeds UI count
    db_total = s.get("db_total", 0) + s.get("db_cancellations", 0)

    row = (
        [week_end.strftime("%d/%m/%Y")] +
        ci_pairs +
        [ci_total, s.get("people_in_house", ""), c_sum] +
        [""] +
        db_pairs +
        [db_total, s.get("last_minute_bookings", 0), s.get("db_beds", ""), db_c_sum] +
        ["", round(s.get("adr", 0), 2), round(s.get("adr_ytd", 0), 2),
         round(s.get("adr_mtd", 0), 2), s.get("long_termers", 0)]
    )
    next_row = _next_perf_row(service, sheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{PERF_TAB}!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()
    ri = next_row - 1
    bg = GREY_LIGHT if ri % 2 == 0 else WHITE
    _batch(service, sheet_id, [
        _fmt(sid, ri, ri + 1, 0, PERF_TOTAL_COLS, bg=bg, fg=BLACK),
        *[_fmt(sid, ri, ri + 1, CI_SRC_START + i*2 + 1, CI_SRC_START + i*2 + 2, fg=RED)
          for i in range(N_DSRC)],
        *[_fmt(sid, ri, ri + 1, DB_SRC_START + i*2 + 1, DB_SRC_START + i*2 + 2, fg=RED)
          for i in range(N_DSRC)],
    ])


# ---------------------------------------------------------------------------
# Occupancy tab
# Layout: Week Ended | Week Occ % | Month | Monthly Occ % | YTD Occ %
# ---------------------------------------------------------------------------

_OCC_HDR = ["Week Ended", "Week Occ %", "Month", "Monthly Occ %", "YTD Occ %"]
OCC_COLS  = len(_OCC_HDR)
PCT_FMT   = {"type": "PERCENT", "pattern": "0.00%"}


def _setup_occupancy(service, sheet_id: str, sid: int):
    _clear_tab(service, sheet_id, OCC_TAB)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{OCC_TAB}!A1",
        valueInputOption="RAW",
        body={"values": [_OCC_HDR]},
    ).execute()
    reqs = [
        _freeze(sid, rows=1, cols=1),
        _fmt(sid, 0, 1, 0, 1,         bg=GREY_DARK,   bold=True, halign="CENTER"),
        _fmt(sid, 0, 1, 1, 3,         bg=PURPLE_D,    bold=True, halign="CENTER"),
        _fmt(sid, 0, 1, 2, 3,         bg=TEAL_D,      bold=True, halign="CENTER"),
        _fmt(sid, 0, 1, 3, 4,         bg=PURPLE_D,    bold=True, halign="CENTER"),
        _fmt(sid, 0, 1, 4, OCC_COLS,  bg=TEAL_D,      bold=True, halign="CENTER"),
        _col_width(sid, 0, 1,  100),
        _col_width(sid, 1, 2,  90),
        _col_width(sid, 2, 3,  75),
        _col_width(sid, 3, 4,  110),
        _col_width(sid, 4, 5,  90),
    ]
    _batch(service, sheet_id, reqs)


def _next_occ_row(service, sheet_id: str) -> int:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{OCC_TAB}!A:A",
    ).execute()
    return len(result.get("values", [])) + 1


def _last_occ_month(service, sheet_id: str) -> str | None:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{OCC_TAB}!C:C",
    ).execute()
    vals = result.get("values", [])
    for v in reversed(vals[1:]):
        if v and v[0]:
            return v[0]
    return None


def _calculate_month_occupancy(rooms_sold: list[dict], month: int, year: int, n_beds: int) -> float:
    """Calculate occupancy % for a specific month from rooms_sold data."""
    from datetime import date, timedelta

    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    month_nights = []
    for d in range((month_end - month_start).days + 1):
        check_date = month_start + timedelta(days=d)
        for r in rooms_sold:
            if r["date"] == check_date:
                month_nights.append(r.get("accommodations_booked", 0))
                break
        else:
            month_nights.append(0)

    if not month_nights or not n_beds:
        return 0.0
    return round(sum(month_nights) / (n_beds * len(month_nights)) * 100, 1)


def _occ_week_exists(service, sheet_id: str, week_end_str: str) -> bool:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{OCC_TAB}!A:A",
    ).execute()
    return any(r and r[0] == week_end_str for r in result.get("values", []))


def append_occupancy(service, sheet_id: str, sid: int, s: dict, week_end: date):
    # Re-running for a week already recorded would append a second row, which
    # shows up as a duplicated point on the occupancy chart. Performance,
    # Reviews and Website Analytics all guard against this; so does this now.
    week_end_str = week_end.strftime("%d/%m/%Y")
    if _occ_week_exists(service, sheet_id, week_end_str):
        print(f"  WARNING: Occupancy row for {week_end_str} already exists -- skipping.")
        return

    month_name = MONTHS[week_end.month - 1]
    last       = _last_occ_month(service, sheet_id)
    is_new     = (month_name != last)

    row = [
        week_end.strftime("%d/%m/%Y"),
        round(s["occ_week"] / 100, 4),
        month_name if is_new else "",
        round(s["occ_month"] / 100, 4) if is_new else "",
        round(s["occ_ytd"] / 100, 4),  # YTD occupancy — calculated from all nightly data YTD
    ]
    next_row = _next_occ_row(service, sheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{OCC_TAB}!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()

    ri = next_row - 1
    bg = GREY_LIGHT if ri % 2 == 0 else WHITE
    reqs = [
        _fmt(sid, ri, ri + 1, 0, OCC_COLS, bg=bg, fg=BLACK),
        _fmt(sid, ri, ri + 1, 1, 2,  num_fmt=PCT_FMT, halign="CENTER"),
        _fmt(sid, ri, ri + 1, 2, 3,  bold=is_new, halign="CENTER"),
        _fmt(sid, ri, ri + 1, 3, 4,  num_fmt=PCT_FMT, halign="CENTER"),
        _fmt(sid, ri, ri + 1, 4, 5,  num_fmt=PCT_FMT, halign="CENTER"),
    ]
    _batch(service, sheet_id, reqs)

    # Update ALL month rows with their occupancy percentages
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{OCC_TAB}!C:C",
    ).execute()
    month_rows = result.get("values", [])

    # Find and update the current month's row with latest monthly occ
    for row_idx, cell in enumerate(month_rows):
        if cell and cell[0] == month_name:
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"{OCC_TAB}!D{row_idx + 1}",
                valueInputOption="USER_ENTERED",
                body={"values": [[round(s["occ_month"] / 100, 4)]]},
            ).execute()
            break


# ---------------------------------------------------------------------------
# Revenue tab
# Layout: Month | 2025 Revenue | 2026 Revenue | Difference ($)
# ---------------------------------------------------------------------------

_REV_HDR  = ["Month", "2025 Revenue ($)", "2026 Revenue ($)", "Difference ($)"]
CUR_FMT   = {"type": "NUMBER", "pattern": '"$"#,##0.00'}
REV_COLS  = len(_REV_HDR)


def _setup_revenue(service, sheet_id: str, sid: int):
    """Write Revenue tab from scratch if not set up."""
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{REV_TAB}!A1:A2",
    ).execute()
    if result.get("values"):
        return   # already exists

    rows = [_REV_HDR]
    for month in MONTHS:
        rows.append([month, None, None, f"=C{len(rows)+1}-B{len(rows)+1}"])

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{REV_TAB}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()

    reqs = [
        _freeze(sid, rows=1),
        # Header
        _fmt(sid, 0, 1, 0, REV_COLS, bg=GREY_DARK, bold=True, halign="CENTER"),
        # Month column
        _fmt(sid, 1, 13, 0, 1, bold=True, fg=BLACK, halign="LEFT"),
        # Currency columns
        _fmt(sid, 1, 13, 1, 4, num_fmt=CUR_FMT, fg=BLACK, halign="RIGHT"),
        # Alternating rows
        *[_fmt(sid, i, i + 1, 0, REV_COLS,
               bg=GREY_LIGHT if i % 2 == 1 else WHITE, fg=BLACK)
          for i in range(1, 13)],
        # Column widths
        _col_width(sid, 0, 1,  70),
        _col_width(sid, 1, 4, 130),
    ]
    _batch(service, sheet_id, reqs)
    print(f"  Revenue tab structure created")


def update_revenue(service, sheet_id: str, month: int, year: int, revenue: float):
    """Update the revenue cell for month+year."""
    col = {2025: "B", 2026: "C"}.get(year)
    if not col:
        return
    row = month + 1   # row 1 = header, row 2 = Jan, etc.
    cell = f"{REV_TAB}!{col}{row}"
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=cell,
        valueInputOption="USER_ENTERED",
        body={"values": [[round(revenue, 2)]]},
    ).execute()


# ---------------------------------------------------------------------------
# Reviews tab
#
# Layout (2-row header):
#   Row 1: Week Ended | Google (merged) | Booking.com (merged) | Hostelworld (merged) | Expedia (merged)
#   Row 2:            | Rating | Reviews | Rating | Reviews     | Rating   | Reviews   | Rating  | Reviews
#   Row 3+: data — date auto-filled, rating + reviews entered manually each week
#
# Ratings scale reminder (shown in header):
#   Google /5  |  Booking.com /10  |  Hostelworld /10  |  Expedia /5
# ---------------------------------------------------------------------------

# Brand-ish colours for each platform
GOOGLE_D   = _rgb("DB4437")   # Google red
GOOGLE_L   = _rgb("FCE8E6")
BOOKING_D  = _rgb("003580")   # Booking.com navy
BOOKING_L  = _rgb("D6E0F5")
HW_D       = _rgb("CC4400")   # Hostelworld orange-red
HW_L       = _rgb("FDEBD0")
EXPEDIA_D  = _rgb("1C3F80")   # Expedia dark blue
EXPEDIA_L  = _rgb("D6E0F5")

_REVIEWS_PLATFORMS = [
    ("Google (/5)",        GOOGLE_D,  GOOGLE_L),
    ("Booking.com (/10)",  BOOKING_D, BOOKING_L),
    ("Hostelworld (/10)",  HW_D,      HW_L),
    ("Expedia (/5)",       EXPEDIA_D, EXPEDIA_L),
]
_REV2_HDR1 = ["Week Ended"] + [p[0] for p in _REVIEWS_PLATFORMS for _ in range(2)]
_REV2_HDR2 = [""]           + ["Rating", "Reviews"] * len(_REVIEWS_PLATFORMS)
REV2_COLS  = 1 + len(_REVIEWS_PLATFORMS) * 2   # 9 total


def _setup_reviews(service, sheet_id: str, sid: int):
    """Build the Reviews tab header + formatting from scratch."""
    _clear_tab(service, sheet_id, REV_TAB2)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{REV_TAB2}!A1",
        valueInputOption="RAW",
        body={"values": [_REV2_HDR1, _REV2_HDR2]},
    ).execute()

    reqs = [
        _freeze(sid, rows=2, cols=1),
        # Week Ended header (both rows)
        _fmt(sid, 0, 2, 0, 1, bg=GREY_DARK, bold=True, fg=WHITE, halign="CENTER", valign="MIDDLE"),
        _col_width(sid, 0, 1, 100),
    ]

    # Merge platform names across their 2 sub-columns + colour both header rows
    for i, (name, dark, light) in enumerate(_REVIEWS_PLATFORMS):
        c = 1 + i * 2
        reqs += [
            _merge(sid, 0, c, c + 2),
            _fmt(sid, 0, 1, c, c + 2, bg=dark,  bold=True, fg=WHITE, halign="CENTER"),
            _fmt(sid, 1, 2, c, c + 2, bg=light, bold=True, fg=BLACK, halign="CENTER"),
            _col_width(sid, c,     c + 1, 75),   # Rating col
            _col_width(sid, c + 1, c + 2, 75),   # Reviews col
        ]

    _batch(service, sheet_id, reqs)
    print(f"  Reviews tab structure created")


def _next_reviews_row(service, sheet_id: str) -> int:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{REV_TAB2}!A:A",
    ).execute()
    return len(result.get("values", [])) + 1


def _existing_review_dates(service, sheet_id: str) -> set[str]:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{REV_TAB2}!A:A",
    ).execute()
    return {row[0] for row in result.get("values", [])[2:] if row and row[0]}


def append_reviews(service, sheet_id: str, sid: int, week_end: date):
    """Append a new row with the week-end date. Rating + review count filled manually."""
    week_end_str = week_end.strftime("%d/%m/%Y")

    # Duplicate guard
    if week_end_str in _existing_review_dates(service, sheet_id):
        print(f"  WARNING: Reviews row for {week_end_str} already exists -- skipping.")
        return

    row = [week_end_str] + ["", ""] * len(_REVIEWS_PLATFORMS)
    next_row = _next_reviews_row(service, sheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{REV_TAB2}!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()

    ri  = next_row - 1
    bg  = GREY_LIGHT if ri % 2 == 0 else WHITE
    reqs = [
        _fmt(sid, ri, ri + 1, 0, REV2_COLS, bg=bg, fg=BLACK),
        _fmt(sid, ri, ri + 1, 0, 1, halign="CENTER"),   # date centred
    ]
    # Light tint on each platform's 2 columns so rows stay readable
    for i, (_, dark, light) in enumerate(_REVIEWS_PLATFORMS):
        c = 1 + i * 2
        reqs.append(_fmt(sid, ri, ri + 1, c, c + 2, halign="CENTER"))
    _batch(service, sheet_id, reqs)


# ---------------------------------------------------------------------------
# Website Analytics tab
#
# 2-row header layout:
#   Row 1: Week Ended | Totals (B-D) | sep | Sessions by Channel (F-K) | sep |
#           Top Countries (M-V) | sep | Age Groups (X-AC) | sep |
#           Gender (AE-AG) | sep | Device Type (AI-AK)
#   Row 2: sub-headers per section
# ---------------------------------------------------------------------------

GA4_D  = _rgb("E37400")   # Google Analytics orange-dark  — Totals
GA4_L  = _rgb("FEF0E3")   # Google Analytics orange-light
WEB_D  = _rgb("1A73E8")   # Google blue-dark              — Channels
WEB_L  = _rgb("D2E3FC")   # Google blue-light
CTR_D  = _rgb("00695C")   # Teal dark                     — Countries
CTR_L  = _rgb("E0F2F1")   # Teal light
AGE_D  = _rgb("4527A0")   # Deep purple                   — Age groups
AGE_L  = _rgb("EDE7F6")   # Lavender
GEN_D  = _rgb("880E4F")   # Dark rose                     — Gender
GEN_L  = _rgb("FCE4EC")   # Light pink
DEV_D  = _rgb("BF360C")   # Deep orange                   — Device type
DEV_L  = _rgb("FBE9E7")   # Light orange

# Channel order must match GA4_CHANNELS in ga4_client.py
WEB_CHANNELS    = ["Direct", "Organic Search", "Organic Social",
                   "Referral", "Paid Other", "Unassigned"]
TOP_N_COUNTRIES = 5
AGE_GROUPS      = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GENDERS         = ["Male", "Female", "Unknown"]
DEVICES         = ["Mobile", "Desktop", "Tablet"]

# Column indices (0-based)
WEB_WEEK_COL   = 0    # A   Week Ended
WEB_SES_COL    = 1    # B   Total Sessions  ─┐
WEB_USR_COL    = 2    # C   Total Users      │ Totals (3 cols)
WEB_PV_COL     = 3    # D   Total Page Views─┘
WEB_SEP_COL    = 4    # E   separator
WEB_CH_START   = 5    # F   Channels (6 cols → K = idx 10)
WEB_SEP2_COL   = 11   # L   separator
WEB_CTR_START  = 12   # M   Countries (5×2 = 10 cols → V = idx 21)
WEB_SEP3_COL   = 22   # W   separator
WEB_AGE_START  = 23   # X   Age groups (6 cols → AC = idx 28)
WEB_SEP4_COL   = 29   # AD  separator
WEB_GEN_START  = 30   # AE  Gender (3 cols → AG = idx 32)
WEB_SEP5_COL   = 33   # AH  separator
WEB_DEV_START  = 34   # AI  Device (3 cols → AK = idx 36)
WEB_TOTAL_COLS = 37

_CTR_SUBHDRS = [v for i in range(TOP_N_COUNTRIES) for v in (f"#{i+1}", "Sessions")]

_WEB_HDR1 = (
    ["Week Ended"] +
    ["Totals", None, None] +
    [""] +
    ["Sessions by Channel"] + [None] * (len(WEB_CHANNELS) - 1) +
    [""] +
    ["Top Countries"] + [None] * (TOP_N_COUNTRIES * 2 - 1) +
    [""] +
    ["Age Groups"] + [None] * (len(AGE_GROUPS) - 1) +
    [""] +
    ["Gender"] + [None] * (len(GENDERS) - 1) +
    [""] +
    ["Device Type"] + [None] * (len(DEVICES) - 1)
)
_WEB_HDR2 = (
    [""] +
    ["Sessions", "Users", "Page Views"] +
    [""] +
    WEB_CHANNELS +
    [""] +
    _CTR_SUBHDRS +
    [""] +
    AGE_GROUPS +
    [""] +
    GENDERS +
    [""] +
    DEVICES
)


def _setup_web_analytics(service, sheet_id: str, sid: int):
    """Build the Website Analytics tab header + formatting from scratch."""
    _clear_tab(service, sheet_id, WEB_TAB)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{WEB_TAB}!A1",
        valueInputOption="RAW",
        body={"values": [_WEB_HDR1, _WEB_HDR2]},
    ).execute()

    reqs = [
        _freeze(sid, rows=2, cols=1),

        # ── Row 1 section headers ───────────────────────────────────────────
        _fmt(sid, 0, 2, WEB_WEEK_COL,  WEB_WEEK_COL + 1,
             bg=GREY_DARK, bold=True, fg=WHITE, halign="CENTER", valign="MIDDLE"),
        _fmt(sid, 0, 1, WEB_SES_COL,   WEB_SEP_COL,
             bg=GA4_D, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, WEB_CH_START,  WEB_SEP2_COL,
             bg=WEB_D, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, WEB_CTR_START, WEB_SEP3_COL,
             bg=CTR_D, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, WEB_AGE_START, WEB_SEP4_COL,
             bg=AGE_D, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, WEB_GEN_START, WEB_SEP5_COL,
             bg=GEN_D, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 0, 1, WEB_DEV_START, WEB_TOTAL_COLS,
             bg=DEV_D, bold=True, fg=WHITE, halign="CENTER"),

        # ── Row 2 sub-headers ───────────────────────────────────────────────
        _fmt(sid, 1, 2, WEB_SES_COL,   WEB_SEP_COL,
             bg=GA4_L, bold=True, fg=BLACK, halign="CENTER"),
        _fmt(sid, 1, 2, WEB_CH_START,  WEB_SEP2_COL,
             bg=WEB_L, bold=True, fg=BLACK, halign="CENTER"),
        _fmt(sid, 1, 2, WEB_CTR_START, WEB_SEP3_COL,
             bg=CTR_L, bold=True, fg=BLACK, halign="CENTER"),
        _fmt(sid, 1, 2, WEB_AGE_START, WEB_SEP4_COL,
             bg=AGE_L, bold=True, fg=BLACK, halign="CENTER"),
        _fmt(sid, 1, 2, WEB_GEN_START, WEB_SEP5_COL,
             bg=GEN_L, bold=True, fg=BLACK, halign="CENTER"),
        _fmt(sid, 1, 2, WEB_DEV_START, WEB_TOTAL_COLS,
             bg=DEV_L, bold=True, fg=BLACK, halign="CENTER"),

        # ── Section header merges (row 1) ───────────────────────────────────
        _merge(sid, 0, WEB_SES_COL,   WEB_SEP_COL),
        _merge(sid, 0, WEB_CH_START,  WEB_SEP2_COL),
        _merge(sid, 0, WEB_CTR_START, WEB_SEP3_COL),
        _merge(sid, 0, WEB_AGE_START, WEB_SEP4_COL),
        _merge(sid, 0, WEB_GEN_START, WEB_SEP5_COL),
        _merge(sid, 0, WEB_DEV_START, WEB_TOTAL_COLS),

        # ── Column widths ───────────────────────────────────────────────────
        _col_width(sid, WEB_WEEK_COL, WEB_WEEK_COL + 1, 100),
        *[_col_width(sid, WEB_SES_COL + i, WEB_SES_COL + i + 1, 90) for i in range(3)],
        _col_width(sid, WEB_SEP_COL,  WEB_SEP_COL  + 1, 15),
        *[_col_width(sid, WEB_CH_START + i, WEB_CH_START + i + 1, 110)
          for i in range(len(WEB_CHANNELS))],
        _col_width(sid, WEB_SEP2_COL, WEB_SEP2_COL + 1, 15),
        *[_col_width(sid, WEB_CTR_START + i * 2,     WEB_CTR_START + i * 2 + 1, 130)
          for i in range(TOP_N_COUNTRIES)],
        *[_col_width(sid, WEB_CTR_START + i * 2 + 1, WEB_CTR_START + i * 2 + 2, 70)
          for i in range(TOP_N_COUNTRIES)],
        _col_width(sid, WEB_SEP3_COL, WEB_SEP3_COL + 1, 15),
        *[_col_width(sid, WEB_AGE_START + i, WEB_AGE_START + i + 1, 70)
          for i in range(len(AGE_GROUPS))],
        _col_width(sid, WEB_SEP4_COL, WEB_SEP4_COL + 1, 15),
        *[_col_width(sid, WEB_GEN_START + i, WEB_GEN_START + i + 1, 80)
          for i in range(len(GENDERS))],
        _col_width(sid, WEB_SEP5_COL, WEB_SEP5_COL + 1, 15),
        *[_col_width(sid, WEB_DEV_START + i, WEB_DEV_START + i + 1, 90)
          for i in range(len(DEVICES))],
    ]
    _batch(service, sheet_id, reqs)
    print(f"  Website Analytics tab structure created")


def _existing_web_dates(service, sheet_id: str) -> set[str]:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{WEB_TAB}!A:A",
    ).execute()
    return {row[0] for row in result.get("values", [])[2:] if row and row[0]}


def _next_web_row(service, sheet_id: str) -> int:
    # Use column B (always populated in both header rows) so the blank A2
    # sub-header cell doesn't cause the data to overwrite row 2.
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{WEB_TAB}!B:B",
    ).execute()
    return len(result.get("values", [])) + 1


def append_web_analytics(service, sheet_id: str, sid: int,
                         ga4_data: dict, week_end: date):
    """Append one week's GA4 traffic + demographics to the Website Analytics tab."""
    week_end_str = week_end.strftime("%d/%m/%Y")

    if week_end_str in _existing_web_dates(service, sheet_id):
        print(f"  WARNING: Web Analytics row for {week_end_str} already exists -- skipping.")
        return

    # Sessions by channel
    channels = ga4_data.get("channels", {})
    channel_sessions = [
        channels.get(ch, {}).get("sessions", 0)
        for ch in WEB_CHANNELS
    ]

    # Top 5 countries — pairs of (country_name, sessions)
    top_countries = ga4_data.get("top_countries", [])
    country_cells: list = []
    for i in range(TOP_N_COUNTRIES):
        if i < len(top_countries):
            name, ses = top_countries[i]
            country_cells += [name, ses]
        else:
            country_cells += ["", ""]

    # Age groups (GA4 stores brackets as-is e.g. "18-24")
    age_data    = ga4_data.get("age_groups", {})
    age_cells   = [age_data.get(ag, 0) for ag in AGE_GROUPS]

    # Gender (GA4 returns lowercase: "male", "female", "unknown")
    gender_data  = ga4_data.get("gender", {})
    gender_cells = [gender_data.get(g.lower(), 0) for g in GENDERS]

    # Device (GA4 returns lowercase: "mobile", "desktop", "tablet")
    device_data  = ga4_data.get("devices", {})
    device_cells = [device_data.get(d.lower(), 0) for d in DEVICES]

    row = (
        [week_end_str] +
        [ga4_data.get("total_sessions",  0),
         ga4_data.get("total_users",     0),
         ga4_data.get("total_pageviews", 0)] +
        [""] +
        channel_sessions +
        [""] +
        country_cells +
        [""] +
        age_cells +
        [""] +
        gender_cells +
        [""] +
        device_cells
    )

    next_row = _next_web_row(service, sheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{WEB_TAB}!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()

    ri = next_row - 1
    bg = GREY_LIGHT if ri % 2 == 0 else WHITE
    _batch(service, sheet_id, [
        _fmt(sid, ri, ri + 1, 0,              WEB_TOTAL_COLS, bg=bg, fg=BLACK),
        _fmt(sid, ri, ri + 1, WEB_WEEK_COL,   WEB_WEEK_COL + 1,  halign="CENTER"),
        _fmt(sid, ri, ri + 1, WEB_SES_COL,    WEB_SEP_COL,       halign="CENTER"),
        _fmt(sid, ri, ri + 1, WEB_CH_START,   WEB_SEP2_COL,      halign="CENTER"),
        _fmt(sid, ri, ri + 1, WEB_CTR_START,  WEB_SEP3_COL,      halign="CENTER"),
        _fmt(sid, ri, ri + 1, WEB_AGE_START,  WEB_SEP4_COL,      halign="CENTER"),
        _fmt(sid, ri, ri + 1, WEB_GEN_START,  WEB_SEP5_COL,      halign="CENTER"),
        _fmt(sid, ri, ri + 1, WEB_DEV_START,  WEB_TOTAL_COLS,    halign="CENTER"),
    ])


# ---------------------------------------------------------------------------
# Platform Reviews tab
# Layout: Date | 4 ratings (B-E) | 4 review counts (F-I)
# Columns A-E are the original layout; F-I were added later, so historical rows
# have blank counts. Readers must carry the last non-blank value forward.
# ---------------------------------------------------------------------------

_PLAT_HDR = ["Date", "Google (/5)", "Booking.com (/10)", "Hostelworld (/10)", "Expedia (/10)",
             "Google #", "Booking.com #", "Hostelworld #", "Expedia #"]
PLAT_COLS = len(_PLAT_HDR)
_PLAT_RATING_COLS = 5              # A-E: date + the four ratings


def _setup_platform_reviews(service, sheet_id: str, sid: int):
    """Create Platform Reviews tab if it doesn't exist."""
    _clear_tab(service, sheet_id, PLAT_TAB)
    _write_plat_header(service, sheet_id)
    reqs = [
        _freeze(sid, rows=1, cols=1),
        _fmt(sid, 0, 1, 0, PLAT_COLS, bg=GREY_DARK, bold=True, halign="CENTER"),
        _col_width(sid, 0, 1, 100),
        _col_width(sid, 1, PLAT_COLS, 120),
    ]
    _batch(service, sheet_id, reqs)
    print(f"  Platform Reviews tab created")


def _write_plat_header(service, sheet_id: str):
    """Write the header row. Idempotent, so it also migrates a tab created
    before the review-count columns existed."""
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{PLAT_TAB}!A1",
        valueInputOption="RAW",
        body={"values": [_PLAT_HDR]},
    ).execute()


def _next_plat_row(service, sheet_id: str) -> int:
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{PLAT_TAB}!A:A",
    ).execute()
    return len(result.get("values", [])) + 1


_PLAT_ORDER = ("google", "booking", "hostelworld", "expedia")


def append_platform_reviews(service, sheet_id: str, sid: int,
                            platform_reviews: dict, week_end: date):
    """Append one week's review scores and counts.

    platform_reviews maps each platform in _PLAT_ORDER to
    {"rating": float|None, "count": int|None}; blanks are written as empty
    cells rather than zeros so a missing fetch never looks like a real drop.
    """
    def cell(name, key):
        value = (platform_reviews.get(name) or {}).get(key)
        return "" if value is None else value

    row = ([week_end.strftime("%d/%m/%Y")]
           + [cell(p, "rating") for p in _PLAT_ORDER]
           + [cell(p, "count") for p in _PLAT_ORDER])

    _write_plat_header(service, sheet_id)     # migrates pre-count tabs in place
    next_row = _next_plat_row(service, sheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{PLAT_TAB}!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()

    ri = next_row - 1
    bg = GREY_LIGHT if ri % 2 == 0 else WHITE
    reqs = [
        _fmt(sid, ri, ri + 1, 0, PLAT_COLS, bg=bg, fg=BLACK),
        _fmt(sid, ri, ri + 1, 0, 1, halign="CENTER"),
        _fmt(sid, ri, ri + 1, 1, _PLAT_RATING_COLS, halign="CENTER",
             num_fmt={"type": "NUMBER", "pattern": "0.0"}),
        _fmt(sid, ri, ri + 1, _PLAT_RATING_COLS, PLAT_COLS, halign="CENTER",
             num_fmt={"type": "NUMBER", "pattern": "#,##0"}),
    ]
    _batch(service, sheet_id, reqs)


# Room Type ADR tab (7-type breakdown with real per-reservation revenue) is
# now owned entirely by build_room_type_adr.py, called directly from
# weekly_report.py -- not through write_report().


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def update_all_month_occupancies(service, sheet_id: str, monthly_occs: dict[str, float]):
    """Update occupancy percentages for all months in the Occupancy tab."""
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{OCC_TAB}!C:D",
    ).execute()
    all_rows = result.get("values", [])

    for row_idx, row in enumerate(all_rows):
        if row and len(row) > 0 and row[0] in MONTHS:
            month_name = row[0]
            if month_name in monthly_occs:
                occ_pct = monthly_occs[month_name]
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=f"{OCC_TAB}!D{row_idx + 1}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[round(occ_pct / 100, 4)]]},
                ).execute()


def write_report(s: dict, week_end: date, monthly_revenues: dict,
                 sheet_id: str = None, ga4_data: dict | None = None, monthly_occs: dict | None = None,
                 platform_reviews: dict | None = None):
    sheet_id = sheet_id or os.environ["GOOGLE_SHEET_ID"]
    service  = _build_service()

    tabs   = _get_tabs(service, sheet_id)
    p_sid  = _ensure_tab(service, sheet_id, PERF_TAB,  tabs)
    o_sid  = _ensure_tab(service, sheet_id, OCC_TAB,   tabs)
    r_sid  = _ensure_tab(service, sheet_id, REV_TAB,   tabs)
    rv_sid = _ensure_tab(service, sheet_id, REV_TAB2,  tabs)
    w_sid  = _ensure_tab(service, sheet_id, WEB_TAB,   tabs)
    plat_sid = _ensure_tab(service, sheet_id, PLAT_TAB, tabs)

    # First-run setup — check B1 (has "Check In") not A1 (intentionally blank)
    perf_header = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{PERF_TAB}!B1:B1",
    ).execute()
    if not perf_header.get("values"):
        _setup_performance(service, sheet_id, p_sid)

    occ_header = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{OCC_TAB}!A1:A1",
    ).execute()
    if not occ_header.get("values"):
        _setup_occupancy(service, sheet_id, o_sid)

    plat_header = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{PLAT_TAB}!A1:A1",
    ).execute()
    if not plat_header.get("values"):
        _setup_platform_reviews(service, sheet_id, plat_sid)

    _setup_revenue(service, sheet_id, r_sid)

    # Reviews tab — create structure if new, always safe to call
    reviews_header = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{REV_TAB2}!A1:A1",
    ).execute()
    if not reviews_header.get("values"):
        _setup_reviews(service, sheet_id, rv_sid)

    # Website Analytics tab — create structure if new
    web_header = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{WEB_TAB}!B1:B1",
    ).execute()
    if not web_header.get("values"):
        _setup_web_analytics(service, sheet_id, w_sid)

    # Duplicate guard — skip if this week's row already exists
    week_end_str = week_end.strftime("%d/%m/%Y")
    existing = _existing_week_dates(service, sheet_id)
    if week_end_str in existing:
        print(f"  WARNING: Week ending {week_end_str} already in Performance tab -- skipping duplicate write.")
        print(f"  -> Performance tab unchanged (duplicate prevented)")
    else:
        append_performance(service, sheet_id, p_sid, s, week_end)
        print(f"  -> Performance tab updated")

    append_occupancy(service, sheet_id, o_sid, s, week_end)
    print(f"  -> Occupancy tab updated")

    append_reviews(service, sheet_id, rv_sid, week_end)
    print(f"  -> Reviews tab updated")

    # Website Analytics tab — only write if GA4 data was provided
    if ga4_data is not None:
        append_web_analytics(service, sheet_id, w_sid, ga4_data, week_end)
        print(f"  -> Website Analytics tab updated")
    else:
        print(f"  -> Website Analytics tab skipped (no GA4 data)")

    # Update revenue for each month we have data for
    for (yr, mo), rev in monthly_revenues.items():
        update_revenue(service, sheet_id, mo, yr, rev)
    print(f"  -> Revenue tab updated")

    # Update all month occupancies if provided
    if monthly_occs:
        update_all_month_occupancies(service, sheet_id, monthly_occs)
        print(f"  -> All month occupancies updated")

    # Update platform reviews if provided
    if platform_reviews:
        append_platform_reviews(service, sheet_id, plat_sid, platform_reviews, week_end)
        print(f"  -> Platform reviews updated")
