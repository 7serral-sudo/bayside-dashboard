#!/usr/bin/env python3
"""
Rebuild the "Room Type ADR" sheet with true per-room-type ADR (all 7 room
types) pulled from Cloudbeds, instead of the old Private-Rooms/Pods split.

Reservation discovery uses an overlap query (checked in on/before the window
end AND checked out on/after the window start), not a check-in-only query, so
long-termers who checked in before the window are not missed. Per-night room
type and rate come from getTransactions' `description` field (frozen at time
of charge), not from getReservation or the transaction's own roomTypeID/
roomTypeName fields -- both of the latter retroactively show a room's CURRENT
type for a reservation's entire history, which silently misattributes revenue
for any stay that outlasted a room being reclassified to a different type
(verified against a real 145-night stay affected by exactly this).

This calls getTransactions once per non-cancelled/overlapping reservation in
the date range (~2000 calls for 2026 YTD, ~15-20 min). Progress is
checkpointed to room_type_adr_cache.json so an interrupted run can resume
without re-fetching reservations already processed.

Usage:
    python build_room_type_adr.py [--from 2026-01-01] [--to 2026-08-04] [--dry-run]
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime

from dotenv import load_dotenv
load_dotenv()

from cloudbeds_client import CloudbedsClient
import sheets_client

CACHE_FILE = "room_type_adr_cache.json"
CANCEL_STATUSES = {"cancelled", "canceled", "no_show"}

GST_RATE = 1.1  # 10% GST, confirmed consistently across every taxed charge observed

# Manually-curated long-termers whose Cloudbeds-recorded nightly rates are
# uneven/lumpy (front-desk data-entry artifacts -- some nights $0, others
# spiking well above their real rate) but who actually pay a fixed weekly
# amount. Verified for both: summing their ACTUAL Cloudbeds dailyRates over
# the full period each rate applied reconciles to within ~$0.26/night of the
# flat weekly rate, so we replace the lumpy per-night amounts with the clean
# flat rate for reporting -- room type attribution (from the transaction
# description) is untouched, only the dollar amount changes.
# reservationID -> {weekly_rate (gross AUD), start (date this rate began)}
FLAT_RATE_OVERRIDES = {
    "1395495305855": {"weekly_rate": 450.0, "start": date(2025, 6, 15)},  # Yusmel Musdelier -- Queen with Ensuite
    "4677276263903": {"weekly_rate": 350.0, "start": date(2026, 6, 25)},  # Sion John Shearer -- Room 20 (rate started 25/06/26; earlier nights were a different, variable rate and are left as recorded)
}


def apply_flat_rate_overrides(reservation_id: str, charges: list[dict]) -> list[dict]:
    override = FLAT_RATE_OVERRIDES.get(reservation_id)
    if not override:
        return charges
    nightly_ex_tax = round(override["weekly_rate"] / GST_RATE / 7, 2)
    for c in charges:
        if c["date"] >= override["start"]:
            c["amount"] = nightly_ex_tax
    return charges

# roomTypeID -> (display name, bed/room count, section)
ROOM_TYPES = {
    "514747": ("4 Bed Dorm",          8,  "dorm"),
    "668675": ("6 Bed Dorm",          42, "dorm"),
    "366327": ("8 Bed Dorm",          16, "dorm"),
    "462944": ("Female Dorm",         12, "dorm"),
    "472073": ("Deluxe Queen",        4,  "private"),
    "515847": ("Queen with Ensuite",  1,  "private"),
    "679030": ("Private (Grd Floor)", 1,  "private"),
}
ROOM_TYPE_ORDER = ["514747", "668675", "366327", "462944", "472073", "515847", "679030"]

# Keyword rules to resolve a transaction description's room-type text back to
# an ID. NOT an exact-string lookup: getRoomTypes only reports each type's
# CURRENT name, but transaction descriptions are frozen at time of charge, so
# older charges can carry an old name (verified: a real charge read "Premium
# Pod Dormitory - 6 Bed", missing "Mixed", which the type's current name has
# -- the type was renamed at some point). Ordered most-specific-first so
# "Queen with Ensuite" isn't caught by a looser "Queen" rule meant for
# "Deluxe Queen".
ROOM_TYPE_KEYWORD_RULES = [
    ("515847", ("ensuite",)),                    # Queen with Ensuite
    ("472073", ("deluxe queen",)),                # Deluxe Queen
    ("679030", ("private room",)),                # Private (Grd Floor)
    ("462944", ("female",)),                      # Female Dorm
    ("514747", ("4 bed",)),                       # 4 Bed Dorm
    ("668675", ("6 bed",)),                       # 6 Bed Dorm
    ("366327", ("8 bed", "8  bed")),              # 8 Bed Dorm (Cloudbeds' own name has a double space)
]


def resolve_room_type_id(name: str) -> str | None:
    lowered = name.lower()
    for rt_id, keywords in ROOM_TYPE_KEYWORD_RULES:
        if any(kw in lowered for kw in keywords):
            return rt_id
    return None

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


CACHE_VERSION = 2


def load_cache() -> dict:
    """Per-reservation contributions, keyed by reservationID.

    v1 stored a pre-summed monthly total plus a list of processed IDs, which
    made a cached reservation impossible to revise: once counted, its nights
    were frozen. A guest STILL IN HOUSE was therefore skipped on every later
    run and never accrued the nights they had since stayed -- Room 20 read
    100% occupied one week and 75.9% the next without ever emptying, because
    its 22 nights stopped growing while the year kept going. A v1 file cannot
    be decomposed back into per-reservation figures, so it is discarded.
    """
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        if cache.get("version") == CACHE_VERSION:
            return cache
        print("  Cache is the old pre-summed format and cannot be revised "
              "per reservation -- rebuilding from scratch.", flush=True)
    return {"version": CACHE_VERSION, "contrib": {}}


def save_cache(cache: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def _aggregate(contrib: dict, res_ids) -> dict:
    """Sum per-reservation contributions into monthly[month][roomType] totals."""
    monthly = defaultdict(lambda: defaultdict(lambda: {"nights": 0, "revenue": 0.0}))
    for res_id in res_ids:
        entry = contrib.get(res_id)
        if not entry:
            continue
        for month_key, types in entry["months"].items():
            for rt_id, vals in types.items():
                monthly[month_key][rt_id]["nights"] += vals["nights"]
                monthly[month_key][rt_id]["revenue"] += vals["revenue"]
    return {mk: {rt: dict(v) for rt, v in types.items()} for mk, types in monthly.items()}


def fetch_and_aggregate(date_from: date, date_to: date) -> dict:
    client = CloudbedsClient()
    cache = load_cache()
    contrib = cache["contrib"]

    print(f"Fetching reservations overlapping {date_from} to {date_to}...", flush=True)
    # Overlap query (not check-in-only) so long-termers who checked in before
    # date_from -- but are still in house or checked out partway through the
    # window -- are not silently missed.
    candidates = client.get_reservations_overlapping(date_from, date_to)
    valid = [r for r in candidates if str(r.get("status", "")).lower() not in CANCEL_STATUSES]

    # A cached reservation is only safe to reuse once its stay has ENDED before
    # the window closes; until then it can still accrue nights, so it must be
    # re-fetched and its contribution replaced. Storing per reservation is what
    # makes replacing possible without double-counting.
    todo, reused = [], 0
    for r in valid:
        res_id = r["reservationID"]
        entry = contrib.get(res_id)
        end = str(r.get("endDate") or "")
        settled = bool(end) and end < date_to.isoformat()
        if entry and settled and entry.get("end") == end:
            reused += 1
        else:
            todo.append(r)

    in_progress = sum(1 for r in todo if contrib.get(r["reservationID"]))
    print(f"  {len(valid)} valid reservations, {reused} settled and reused, "
          f"{len(todo)} to fetch ({in_progress} of them re-fetched because the "
          f"stay was still open)", flush=True)

    fail_count = 0
    unmapped_count = 0
    for i, r in enumerate(todo):
        res_id = r["reservationID"]

        try:
            charges = client.get_reservation_room_charges(res_id, date_from, date_to)
        except Exception as e:
            fail_count += 1
            print(f"  [{i+1}/{len(todo)}] SKIP {res_id}: {e}", flush=True)
            continue

        charges = apply_flat_rate_overrides(res_id, charges)

        # Each entry from get_reservation_room_charges is already one
        # bed-night (sourced from dailyRates, one row per bed per night --
        # not deduped by date, since a group/family reservation can have
        # multiple beds occupied on the same calendar date). Do NOT
        # collapse by date here -- that previously undercounted group
        # bookings by keeping only one bed's night per date instead of all
        # of them (verified against Data Insights: 65 occupied beds on a
        # test day vs only 60 surfaced after date-deduping).
        months = defaultdict(lambda: defaultdict(lambda: {"nights": 0, "revenue": 0.0}))
        for c in charges:
            night_date = c["date"]
            if not (date_from <= night_date <= date_to):
                continue  # outside the window -- part of a long stay that started earlier / ends later
            rt_id = resolve_room_type_id(c["roomTypeName"])
            if rt_id is None:
                unmapped_count += 1
                continue
            month_key = f"{night_date.year}-{night_date.month:02d}"
            months[month_key][rt_id]["nights"] += 1
            months[month_key][rt_id]["revenue"] += c["amount"]

        # Replaces any earlier contribution for this reservation outright.
        contrib[res_id] = {
            "end": str(r.get("endDate") or ""),
            "months": {mk: {rt: dict(v) for rt, v in types.items()}
                       for mk, types in months.items()},
        }

        if (i + 1) % 50 == 0 or (i + 1) == len(todo):
            print(f"  [{i+1}/{len(todo)}] processed ({fail_count} failed, {unmapped_count} unmapped nights so far), checkpointing...", flush=True)
            save_cache(cache)

    save_cache(cache)

    # Only reservations in this window count towards the totals, so a stale
    # cache entry from an earlier window can never leak into the result.
    return _aggregate(contrib, [r["reservationID"] for r in valid])


# -- Sheet writing ------------------------------------------------------------

def _rgb(hex_str: str) -> dict:
    h = hex_str.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}

WHITE, BLACK = _rgb("FFFFFF"), _rgb("000000")
GREY_DARK, GREY_LIGHT = _rgb("404040"), _rgb("F2F2F2")
TEAL_D, TEAL_L = _rgb("1F5C5C"), _rgb("D5EFEF")
NAVY_D, NAVY_L = _rgb("1F3C5C"), _rgb("D5E2EF")


def _rng(sid, r0, r1, c0, c1):
    return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}


def _fmt(sid, r0, r1, c0, c1, bg=None, bold=False, fg=WHITE, halign=None, num_fmt=None):
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
    if num_fmt:
        fmt["numberFormat"] = num_fmt
    fields_parts = []
    if bg: fields_parts.append("backgroundColor")
    if tf: fields_parts.append("textFormat")
    if halign: fields_parts.append("horizontalAlignment")
    if num_fmt: fields_parts.append("numberFormat")
    return {"repeatCell": {"range": _rng(sid, r0, r1, c0, c1), "cell": {"userEnteredFormat": fmt},
                            "fields": "userEnteredFormat(" + ",".join(fields_parts) + ")"}}


def _merge(sid, r0, c0, c1):
    return {"mergeCells": {"range": _rng(sid, r0, r0 + 1, c0, c1), "mergeType": "MERGE_ALL"}}


def _col_width(sid, c0, c1, px):
    return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c1},
                                           "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def write_sheet(monthly: dict, months_present: list[str]):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    service = sheets_client._build_service()
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    sid = tabs["Room Type ADR"]

    service.spreadsheets().values().clear(spreadsheetId=sheet_id, range="Room Type ADR!A1:ZZ").execute()

    n_types = len(ROOM_TYPE_ORDER)
    private_beds = sum(b for _, b, sec in ROOM_TYPES.values() if sec == "private")
    dorm_beds = sum(b for _, b, sec in ROOM_TYPES.values() if sec == "dorm")

    # Column layout: Month | Private Rooms summary | Pods summary | 7 individual types | All Rooms total
    header1 = ["Month", f"Private Rooms ({private_beds} total)", "", f"Pods ({dorm_beds} total)", ""]
    header2 = ["", "Nights", "ADR ($)", "Nights", "ADR ($)"]
    for rt_id in ROOM_TYPE_ORDER:
        name, beds, _ = ROOM_TYPES[rt_id]
        unit = "beds" if beds > 1 else "room"
        header1 += [f"{name} ({beds} {unit})", ""]
        header2 += ["Nights", "ADR ($)"]
    header1 += ["All Rooms (84 total)", ""]
    header2 += ["Nights", "ADR ($)"]

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="Room Type ADR!A1", valueInputOption="RAW",
        body={"values": [header1, header2]},
    ).execute()

    reqs = [
        _fmt(sid, 0, 1, 0, 1, bg=GREY_DARK, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 1, 2, 0, 1, bg=GREY_DARK, bold=True, fg=WHITE, halign="CENTER"),
        _col_width(sid, 0, 1, 90),
        # Private Rooms summary (cols 1-2)
        _merge(sid, 0, 1, 3),
        _fmt(sid, 0, 1, 1, 3, bg=TEAL_D, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 1, 2, 1, 3, bg=TEAL_L, bold=True, fg=BLACK, halign="CENTER"),
        _col_width(sid, 1, 3, 100),
        # Pods summary (cols 3-4)
        _merge(sid, 0, 3, 5),
        _fmt(sid, 0, 1, 3, 5, bg=NAVY_D, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 1, 2, 3, 5, bg=NAVY_L, bold=True, fg=BLACK, halign="CENTER"),
        _col_width(sid, 3, 5, 100),
    ]
    detail_start = 5
    for idx, rt_id in enumerate(ROOM_TYPE_ORDER):
        c0 = detail_start + idx * 2
        section = ROOM_TYPES[rt_id][2]
        bg = TEAL_D if section == "private" else NAVY_D
        bg_l = TEAL_L if section == "private" else NAVY_L
        reqs += [
            _merge(sid, 0, c0, c0 + 2),
            _fmt(sid, 0, 1, c0, c0 + 2, bg=bg, bold=True, fg=WHITE, halign="CENTER"),
            _fmt(sid, 1, 2, c0, c0 + 2, bg=bg_l, bold=True, fg=BLACK, halign="CENTER"),
            _col_width(sid, c0, c0 + 2, 95),
        ]
    c0 = detail_start + n_types * 2
    reqs += [
        _merge(sid, 0, c0, c0 + 2),
        _fmt(sid, 0, 1, c0, c0 + 2, bg=GREY_DARK, bold=True, fg=WHITE, halign="CENTER"),
        _fmt(sid, 1, 2, c0, c0 + 2, bg=GREY_LIGHT, bold=True, fg=BLACK, halign="CENTER"),
        _col_width(sid, c0, c0 + 2, 100),
    ]

    def _section_ids(section):
        return [rt_id for rt_id in ROOM_TYPE_ORDER if ROOM_TYPES[rt_id][2] == section]

    PRIVATE_IDS = _section_ids("private")
    DORM_IDS = _section_ids("dorm")

    rows = []
    ytd_totals = defaultdict(lambda: {"nights": 0, "revenue": 0.0})

    for month_key in months_present:
        month_num = int(month_key.split("-")[1])
        month_name = MONTH_NAMES[month_num - 1]
        data = monthly.get(month_key, {})

        def _sum(ids):
            n = sum(data.get(i, {"nights": 0, "revenue": 0.0})["nights"] for i in ids)
            r = sum(data.get(i, {"nights": 0, "revenue": 0.0})["revenue"] for i in ids)
            return n, r

        priv_nights, priv_revenue = _sum(PRIVATE_IDS)
        pods_nights, pods_revenue = _sum(DORM_IDS)
        priv_adr = round(priv_revenue / priv_nights, 2) if priv_nights else 0
        pods_adr = round(pods_revenue / pods_nights, 2) if pods_nights else 0

        row = [month_name, priv_nights, priv_adr, pods_nights, pods_adr]
        all_nights, all_revenue = 0, 0.0
        for rt_id in ROOM_TYPE_ORDER:
            v = data.get(rt_id, {"nights": 0, "revenue": 0.0})
            nights, revenue = v["nights"], v["revenue"]
            adr = round(revenue / nights, 2) if nights else 0
            row += [nights, adr]
            ytd_totals[rt_id]["nights"] += nights
            ytd_totals[rt_id]["revenue"] += revenue
            all_nights += nights
            all_revenue += revenue
        all_adr = round(all_revenue / all_nights, 2) if all_nights else 0
        row += [all_nights, all_adr]
        rows.append(row)

    ytd_priv_nights = sum(ytd_totals[i]["nights"] for i in PRIVATE_IDS)
    ytd_priv_revenue = sum(ytd_totals[i]["revenue"] for i in PRIVATE_IDS)
    ytd_pods_nights = sum(ytd_totals[i]["nights"] for i in DORM_IDS)
    ytd_pods_revenue = sum(ytd_totals[i]["revenue"] for i in DORM_IDS)
    ytd_priv_adr = round(ytd_priv_revenue / ytd_priv_nights, 2) if ytd_priv_nights else 0
    ytd_pods_adr = round(ytd_pods_revenue / ytd_pods_nights, 2) if ytd_pods_nights else 0

    ytd_row = ["YTD", ytd_priv_nights, ytd_priv_adr, ytd_pods_nights, ytd_pods_adr]
    ytd_all_nights, ytd_all_revenue = 0, 0.0
    for rt_id in ROOM_TYPE_ORDER:
        nights = ytd_totals[rt_id]["nights"]
        revenue = ytd_totals[rt_id]["revenue"]
        adr = round(revenue / nights, 2) if nights else 0
        ytd_row += [nights, adr]
        ytd_all_nights += nights
        ytd_all_revenue += revenue
    ytd_all_adr = round(ytd_all_revenue / ytd_all_nights, 2) if ytd_all_nights else 0
    ytd_row += [ytd_all_nights, ytd_all_adr]
    rows.append(ytd_row)

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="Room Type ADR!A3", valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()

    n_cols = 1 + (2 + n_types + 1) * 2
    for row_idx in range(len(rows)):
        ri = row_idx + 2
        bg = GREY_LIGHT if ri % 2 == 0 else WHITE
        is_ytd = (row_idx == len(rows) - 1)
        reqs += [
            _fmt(sid, ri, ri + 1, 0, n_cols, bg=bg, fg=BLACK),
            _fmt(sid, ri, ri + 1, 0, 1, bold=is_ytd, halign="CENTER"),
        ]
        for c in range(1, n_cols, 2):
            reqs += [
                _fmt(sid, ri, ri + 1, c, c + 1, halign="RIGHT"),
                _fmt(sid, ri, ri + 1, c + 1, c + 2, num_fmt={"type": "NUMBER", "pattern": '"$"#,##0.00'}, halign="RIGHT"),
            ]

    service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": reqs}).execute()
    print(f"Updated Room Type ADR tab: {len(rows)} rows x {n_cols} columns")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{sheet_id}")


WEEK_ADR_TAB = "Room Type ADR Weekly"


def ytd_section_adr(monthly: dict, months_present: list[str]) -> dict:
    """Blended private/pod ADR across every month in `monthly` -- the same
    roll-up write_sheet()'s YTD row uses, factored out so the weekly
    snapshot below can reuse it without an extra Cloudbeds fetch (the
    `monthly` dict passed in already covers the full YTD range)."""
    totals = {"private": {"nights": 0, "revenue": 0.0}, "dorm": {"nights": 0, "revenue": 0.0}}
    for mk in months_present:
        for rt_id, v in monthly[mk].items():
            if rt_id not in ROOM_TYPES:
                continue
            section = ROOM_TYPES[rt_id][2]
            totals[section]["nights"] += v["nights"]
            totals[section]["revenue"] += v["revenue"]

    def _adr(section):
        n = totals[section]["nights"]
        return round(totals[section]["revenue"] / n, 2) if n else None

    return {"private_adr": _adr("private"), "pods_adr": _adr("dorm")}


def fetch_week_section_adr(week_start: date, week_end: date) -> dict:
    """Blended private/pod ADR for a single 7-day window, live from Cloudbeds.

    Deliberately its OWN fetch with no cache -- fetch_and_aggregate()'s cache
    is keyed by reservation ID and stores each reservation's contribution
    scoped to whatever date range it was last called with. Calling that
    function again with a much narrower window (a week, instead of the YTD
    range weekly_report.py normally passes) would overwrite a still-open
    reservation's cached full-year contribution with just this week's
    slice, silently corrupting every other month's YTD/monthly ADR figures.
    A week's fetch takes ~60-90s (mostly still-open reservations that can
    never be cached anyway), which is fine for a once-a-week job but far too
    slow to repeat on every dashboard build -- this must only be called from
    the weekly pipeline, never from build_dashboard.py.
    """
    client = CloudbedsClient()
    candidates = client.get_reservations_overlapping(week_start, week_end)
    valid = [r for r in candidates if str(r.get("status", "")).lower() not in CANCEL_STATUSES]

    totals = {"private": {"nights": 0, "revenue": 0.0}, "dorm": {"nights": 0, "revenue": 0.0}}
    for r in valid:
        res_id = r["reservationID"]
        try:
            charges = client.get_reservation_room_charges(res_id, week_start, week_end)
        except Exception:
            continue
        charges = apply_flat_rate_overrides(res_id, charges)
        for c in charges:
            night_date = c["date"]
            if not (week_start <= night_date <= week_end):
                continue
            rt_id = resolve_room_type_id(c["roomTypeName"])
            if rt_id is None or rt_id not in ROOM_TYPES:
                continue
            section = ROOM_TYPES[rt_id][2]
            totals[section]["nights"] += 1
            totals[section]["revenue"] += c["amount"]

    def _adr(section):
        n = totals[section]["nights"]
        return round(totals[section]["revenue"] / n, 2) if n else None

    return {
        "private_adr": _adr("private"), "pods_adr": _adr("dorm"),
        "private_nights": totals["private"]["nights"], "private_revenue": totals["private"]["revenue"],
        "pods_nights": totals["dorm"]["nights"], "pods_revenue": totals["dorm"]["revenue"],
    }


def append_week_adr(week_end: date, private_adr, pods_adr, private_ytd_adr, pods_ytd_adr, log=print):
    """Appends one row to the "Room Type ADR Weekly" tab, skipping if this
    week_end is already recorded (same duplicate-row guard used for
    Occupancy/Performance/Reviews/Website Analytics).

    Stores both this single week's blended ADR AND the running YTD ADR as
    of this week -- they answer different questions. The week figure says
    how this week alone traded; the YTD snapshot lets next week's dashboard
    show whether the YTD number itself (the one actually displayed, e.g.
    "$70.60") is climbing or sliding, which a single week's ADR can't tell
    you on its own.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    service = sheets_client._build_service()
    existing = sheets_client._get_tabs(service, sheet_id)
    sid = sheets_client._ensure_tab(service, sheet_id, WEEK_ADR_TAB, existing)

    week_end_str = week_end.strftime("%d/%m/%Y")
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{WEEK_ADR_TAB}!A2:A",
    ).execute()
    existing_dates = [r[0] for r in result.get("values", []) if r]
    if week_end_str in existing_dates:
        log(f"  WARNING: Room Type ADR Weekly row for {week_end_str} already exists -- skipping.")
        return

    if not existing_dates:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"{WEEK_ADR_TAB}!A1", valueInputOption="RAW",
            body={"values": [["Week ending", "Private ADR", "Pod ADR", "Private ADR YTD", "Pod ADR YTD"]]},
        ).execute()

    service.spreadsheets().values().append(
        spreadsheetId=sheet_id, range=f"{WEEK_ADR_TAB}!A2:E",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [[week_end_str, private_adr, pods_adr, private_ytd_adr, pods_ytd_adr]]},
    ).execute()
    log(f"  -> Room Type ADR Weekly: {week_end_str} private ${private_adr} (YTD ${private_ytd_adr}) "
        f"· pods ${pods_adr} (YTD ${pods_ytd_adr})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2026-01-01")
    ap.add_argument("--to", dest="date_to", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)

    monthly = fetch_and_aggregate(date_from, date_to)
    months_present = sorted(monthly.keys())

    print("\nSummary by room type (YTD):")
    ytd = defaultdict(lambda: {"nights": 0, "revenue": 0.0})
    for mk in months_present:
        for rt_id, v in monthly[mk].items():
            if rt_id in ROOM_TYPES:
                ytd[rt_id]["nights"] += v["nights"]
                ytd[rt_id]["revenue"] += v["revenue"]
    for rt_id in ROOM_TYPE_ORDER:
        name = ROOM_TYPES[rt_id][0]
        nights = ytd[rt_id]["nights"]
        revenue = ytd[rt_id]["revenue"]
        adr = revenue / nights if nights else 0
        print(f"  {name:22} {nights:5} nights   ${revenue:10,.2f} revenue   ADR ${adr:6.2f}")

    if args.dry_run:
        print("\nDry run -- not writing to sheet.")
        return

    write_sheet(monthly, months_present)


if __name__ == "__main__":
    main()
