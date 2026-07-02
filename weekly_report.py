"""
Bayside House -- Weekly Hostel Report
Runs every Wednesday. Covers the 7 days ending yesterday (Wed run = Wed-Tue window).
Usage:  python weekly_report.py             # auto date range
        python weekly_report.py 2026-05-05  # override week-end date
"""
import os
import sys
from datetime import date, timedelta, datetime
from collections import defaultdict


def log(msg: str = ""):
    """Print with a timestamp prefix so every log line is traceable."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

from dotenv import load_dotenv
load_dotenv()

from cloudbeds_client import CloudbedsClient
from ga4_client import GA4Client
import sheets_client
import html_report

# ---------------------------------------------------------------------------
# Source normalisation
# ---------------------------------------------------------------------------

SOURCE_MAP = {
    "booking.com": "Booking.com",
    "bookingcom":  "Booking.com",
    "expedia":     "Expedia",
    "hostelworld": "HostelWorld",
    "hostel world":"HostelWorld",
    "agoda":       "Agoda",
    "website":     "Website",
    "direct":      "Website",
    "walk-in":     "Walk-In",
    "walkin":      "Walk-In",
    "walk in":     "Walk-In",
    "phone":       "Phone",
    "telephone":   "Phone",
}

SOURCES = ["Booking.com", "Expedia", "HostelWorld", "Agoda", "Website", "Walk-In", "Phone"]


def normalise_source(raw: str) -> str:
    key = (raw or "").strip().lower()
    for fragment, label in SOURCE_MAP.items():
        if fragment in key:
            return label
    return "Other"


# ---------------------------------------------------------------------------
# Occupancy helpers
# ---------------------------------------------------------------------------

def occ_pct_from_counts(nightly_counts: list[int], n_beds: int) -> float:
    """Occupancy % from a list of per-night occupied bed counts."""
    if not nightly_counts or not n_beds:
        return 0.0
    return round(sum(nightly_counts) / (n_beds * len(nightly_counts)) * 100, 1)


# ---------------------------------------------------------------------------
# Bed-nights booked (stay duration across a list of reservations)
# ---------------------------------------------------------------------------

def beds_booked(reservations: list[dict]) -> int:
    """Sum of (adults × nights) across all reservations — individual bed-nights."""
    total = 0
    for r in reservations:
        try:
            ci    = date.fromisoformat(r["startDate"])
            co    = date.fromisoformat(r["endDate"])
            nights = max((co - ci).days, 0)
            adults = int(r.get("adults", 1) or 1)
            total += nights * adults
        except (KeyError, ValueError):
            continue
    return total


# ---------------------------------------------------------------------------
# Source breakdown
# ---------------------------------------------------------------------------

def source_counts(reservations: list[dict], statuses: set[str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in reservations:
        status = str(r.get("status", "")).lower()
        if statuses and status not in statuses:
            continue
        counts[normalise_source(r.get("sourceName", ""))] += 1
    return counts


def source_beds(reservations: list[dict], statuses: set[str] | None = None) -> dict[str, int]:
    """Total bed-nights per source."""
    counts: dict[str, int] = defaultdict(int)
    for r in reservations:
        status = str(r.get("status", "")).lower()
        if statuses and status not in statuses:
            continue
        try:
            ci = date.fromisoformat(r["startDate"])
            co = date.fromisoformat(r["endDate"])
            nights = max((co - ci).days, 0)
        except (KeyError, ValueError):
            nights = 0
        counts[normalise_source(r.get("sourceName", ""))] += nights
    return counts


# ---------------------------------------------------------------------------
# Main crunch
# ---------------------------------------------------------------------------

def crunch(
    arrivals_week:    list[dict],  # all arrivals this week (any status, by check-in date)
    bookings_week:    list[dict],  # same as arrivals (used for summary counts)
    bookings_created: list[dict],  # reservations where dateCreated falls in the week window
    people_eow:       int,         # accommodations booked at week end (from rooms_sold)
    nightly_week:     list[int],   # occupied beds each night of the week (7 values)
    nightly_month:    list[int],   # occupied beds each night of the month (MTD)
    nightly_ytd:      list[int],   # occupied beds each night year-to-date
    di_rev_week:      float,       # room revenue this week from Data Insights
    di_rev_month:     float,       # room revenue MTD from Data Insights
    di_rev_ly:        float,       # room revenue same week last year from Data Insights
    di_rev_ytd:       float,       # room revenue YTD from Data Insights
    n_beds:           int,
    week_start:       date,
    week_end:         date,
    db_beds_actual:   int = 0,  # real bed-assignment count for Date Booked (from Cloudbeds room data)
) -> dict:

    n_days_week = 7

    CANCEL_S = {"cancelled", "canceled", "no_show"}

    # -- Arrivals split by status
    cancelled_arr = [r for r in arrivals_week
                     if str(r.get("status","")).lower() in CANCEL_S]
    checkins_arr  = [r for r in arrivals_week
                     if str(r.get("status","")).lower() not in CANCEL_S]

    # -- Bookings split by status
    cancelled_bk = [r for r in bookings_week
                    if str(r.get("status","")).lower() in CANCEL_S]

    # -- Weekly summary
    total_bookings_week  = len(bookings_week) - len(cancelled_bk)
    total_checkins_week  = len(checkins_arr)    # confirmed only (cancellations shown per-source)
    beds_booked_bookings = beds_booked(bookings_week)
    beds_booked_checkins = beds_booked(checkins_arr)

    people_in_house = people_eow

    # -- Revenue from Data Insights (matches Cloudbeds UI exactly)
    rev_week  = di_rev_week
    rev_month = di_rev_month
    rev_ly    = di_rev_ly

    # ADR = Data Insights room revenue / occupied bed-nights (matches Cloudbeds UI)
    obn_week = sum(nightly_week)
    adr = di_rev_week / obn_week if obn_week else 0

    # RevPAR = Data Insights room revenue / available bed-nights this week
    avail_week = n_beds * n_days_week
    revpar = di_rev_week / avail_week if avail_week else 0

    # Dates booked = total stay-nights represented by this week's bookings
    dates_booked = beds_booked_bookings

    # Revenue vs last year
    rev_diff = rev_week - rev_ly

    # -- Occupancy
    # Weekly: average the per-month occupancy % across months touched by the week
    # (matches Cloudbeds month-grouped rooms_sold report for the same period)
    month_buckets: dict[int, list[int]] = defaultdict(list)
    for d, c in zip([week_start + timedelta(days=i) for i in range(7)], nightly_week):
        month_buckets[d.month].append(c)
    monthly_occs = [
        sum(counts) / (n_beds * len(counts)) * 100
        for counts in month_buckets.values()
    ]
    occ_week  = round(sum(monthly_occs) / len(monthly_occs), 3)
    occ_month = occ_pct_from_counts(nightly_month, n_beds)
    occ_ytd   = occ_pct_from_counts(nightly_ytd, n_beds)

    # -- Check-in source breakdown
    # ci = confirmed check-ins per source; ci_cancel = cancellations per source
    ci_src        = source_counts(checkins_arr)            # confirmed only
    ci_cancel_src = source_counts(cancelled_arr)           # cancellations per source
    ci_cancellations = len(cancelled_arr)                  # total cancellations

    # -- Bookings source breakdown
    bk_src        = source_counts([r for r in bookings_week if str(r.get("status","")).lower() not in CANCEL_S])
    bk_cancel_src = source_counts([r for r in bookings_week if str(r.get("status","")).lower() in CANCEL_S])
    bk_cancellations = len(cancelled_bk)

    # YTD ADR
    obn_ytd  = sum(nightly_ytd)
    adr_ytd  = round(di_rev_ytd / obn_ytd, 2) if obn_ytd else 0

    # -- Date Booked breakdown (reservations created this week, by source)
    db_confirmed = [r for r in bookings_created
                    if str(r.get("status", "")).lower() not in CANCEL_S]
    db_cancelled = [r for r in bookings_created
                    if str(r.get("status", "")).lower() in CANCEL_S]
    db_src        = source_counts(db_confirmed)
    db_cancel_src = source_counts(db_cancelled)
    db_beds       = db_beds_actual   # real room/bed-assignment count, not bed-nights

    # -- Last-minute bookings: checked in this week AND booked this week.
    # Built from checkins_arr (server-filtered via checkInFrom/checkInTo, reliable
    # for any date range) rather than bookings_created (which relies on Cloudbeds
    # returning results newest-first to know when to stop paginating -- that
    # assumption breaks down for older weeks and silently returns garbage).
    last_minute_count = 0
    for r in checkins_arr:
        dc_str = r.get("dateCreated", "")
        if not dc_str:
            continue
        try:
            dc_date = date.fromisoformat(dc_str[:10])
        except ValueError:
            continue
        if week_start <= dc_date <= week_end:
            last_minute_count += 1

    return {
        # Weekly summary
        "total_bookings":        total_bookings_week,
        "beds_booked_bookings":  beds_booked_bookings,
        "total_checkins":        total_checkins_week,
        "beds_booked_checkins":  beds_booked_checkins,
        "adr":                   round(adr, 2),
        "adr_ytd":               adr_ytd,
        "people_in_house":       people_in_house,
        # Check-ins by source: confirmed count + cancellations per source
        "ci":            {s: ci_src.get(s, 0)        for s in SOURCES},
        "ci_cancel":     {s: ci_cancel_src.get(s, 0) for s in SOURCES},
        "ci_cancellations": ci_cancellations,
        # Bookings by source: confirmed count + cancellations per source
        "bk":            {s: bk_src.get(s, 0)        for s in SOURCES},
        "bk_cancel":     {s: bk_cancel_src.get(s, 0) for s in SOURCES},
        "bk_cancellations": bk_cancellations,
        # Date Booked by source
        "db":              {s: db_src.get(s, 0)        for s in SOURCES},
        "db_cancel":       {s: db_cancel_src.get(s, 0) for s in SOURCES},
        "db_total":        len(db_confirmed),
        "db_cancellations": len(db_cancelled),
        "db_beds":         db_beds,
        "last_minute_bookings": last_minute_count,
        # Occupancy
        "occ_week":   occ_week,
        "occ_month":  occ_month,
        "occ_ytd":    occ_ytd,
        # Revenue
        "rev_week":   round(rev_week, 2),
        "rev_month":  round(rev_month, 2),
        "rev_ly":     round(rev_ly, 2),
        "rev_diff":   round(rev_diff, 2),
        "revpar":     round(revpar, 2),
        "dates_booked": dates_booked,
        # Meta
        "n_beds":     n_beds,
        "obn_week":   obn_week,
    }


# ---------------------------------------------------------------------------
# Print report
# ---------------------------------------------------------------------------

def fc(v): return f"${v:,.2f}"
def fp(v): return f"{v:.1f}%"
def yoy(v):
    if v > 0: return f"^ +{fc(v)}"
    if v < 0: return f"v -{fc(abs(v))}"
    return "-- $0.00"


def print_report(s: dict, week_start: date, week_end: date):
    sep = "=" * 56
    print(f"\n{sep}")
    print(f"  BAYSIDE HOUSE -- WEEKLY REPORT")
    print(f"  {week_start.strftime('%d %b %Y')} to {week_end.strftime('%d %b %Y')}")
    print(f"  Total beds: {s['n_beds']}   Occupied bed-nights: {s['obn_week']}")
    print(sep)

    print(f"\n  WEEKLY SUMMARY")
    print(f"  {'Total Bookings':<32} {s['total_bookings']:>6}")
    print(f"  {'Total Beds Booked (Bookings)':<32} {s['beds_booked_bookings']:>6}")
    print(f"  {'Total Check-Ins':<32} {s['total_checkins']:>6}")
    print(f"  {'Total Beds Booked (Check-ins)':<32} {s['beds_booked_checkins']:>6}")
    print(f"  {'ADR (Avg Bed Night Rate)':<32} {fc(s['adr']):>10}")
    print(f"  {'People in House (end of week)':<32} {s['people_in_house']:>6}")

    print(f"\n  CHECK-INS BY SOURCE")
    for src in SOURCES:
        print(f"  {src:<32} {s['ci'].get(src, 0):>6}")
    print(f"  {'Cancellations':<32} {s['ci_cancellations']:>6}")
    print(f"  {'TOTAL CHECK-INS':<32} {s['total_checkins']:>6}")

    print(f"\n  BOOKINGS BY SOURCE")
    for src in SOURCES:
        print(f"  {src:<32} {s['bk'].get(src, 0):>6}")
    print(f"  {'Cancellations':<32} {s['bk_cancellations']:>6}")
    print(f"  {'TOTAL BOOKINGS':<32} {s['total_bookings']:>6}")

    print(f"\n  OCCUPANCY")
    print(f"  {'This week':<32} {fp(s['occ_week']):>10}")
    print(f"  {'This month (MTD)':<32} {fp(s['occ_month']):>10}")
    print(f"  {'YTD average':<32} {fp(s['occ_ytd']):>10}")

    print(f"\n  REVENUE (AUD)")
    print(f"  {'Total Revenue - This week':<32} {fc(s['rev_week']):>12}")
    print(f"  {'Total Revenue - This month':<32} {fc(s['rev_month']):>12}")
    print(f"  {'Same week last year':<32} {fc(s['rev_ly']):>12}")
    print(f"  {'Difference vs last year':<32} {yoy(s['rev_diff']):>12}")
    print(f"  {'RevPAR':<32} {fc(s['revpar']):>12}")
    print(f"  {'Dates Booked (bed-nights)':<32} {s['dates_booked']:>12}")

    print(f"\n{sep}\n")


# ---------------------------------------------------------------------------
# Google Sheet row
# ---------------------------------------------------------------------------

SHEET_COLUMNS = [
    "Week Ending", "Week Starting",
    # Weekly summary
    "Total Bookings", "Total Beds Booked (Bookings)",
    "Total Check-Ins", "Total Beds Booked (Check-ins)",
    "ADR", "People in House",
    # Check-ins by source
    "Booking.com CI", "Expedia CI", "HostelWorld CI", "Agoda CI",
    "Website CI", "Walk-ins & Phone CI", "Cancellations (CI)", "Total Check-ins",
    # Bookings by source
    "Booking.com BK", "Expedia BK", "HostelWorld BK", "Agoda BK",
    "Website BK", "Walk-ins & Phone BK", "Cancellations (BK)", "Total Bookings",
    # Occupancy
    "Occupancy % Week", "Occupancy % Month", "YTD Occupancy %",
    # Revenue
    "Revenue Week", "Revenue Month",
    "Revenue Last Year (same week)", "Difference vs Last Year ($)",
    "RevPAR", "Dates Booked (bed-nights)",
]


def build_sheet_row(s: dict, week_start: date, week_end: date) -> list:
    return [
        week_end.isoformat(), week_start.isoformat(),
        # Weekly summary
        s["total_bookings"], s["beds_booked_bookings"],
        s["total_checkins"], s["beds_booked_checkins"],
        s["adr"], s["people_in_house"],
        # Check-ins by source
        s["ci"].get("Booking.com", 0), s["ci"].get("Expedia", 0),
        s["ci"].get("HostelWorld", 0), s["ci"].get("Agoda", 0),
        s["ci"].get("Website", 0),
        s["ci"].get("Walk-In", 0) + s["ci"].get("Phone", 0),  # combined Walk + Ph
        s["ci_cancellations"], s["total_checkins"],
        # Bookings by source
        s["bk"].get("Booking.com", 0), s["bk"].get("Expedia", 0),
        s["bk"].get("HostelWorld", 0), s["bk"].get("Agoda", 0),
        s["bk"].get("Website", 0),
        s["bk"].get("Walk-In", 0) + s["bk"].get("Phone", 0),  # combined Walk + Ph
        s["bk_cancellations"], s["total_bookings"],
        # Occupancy
        s["occ_week"], s["occ_month"], s["occ_ytd"],
        # Revenue
        s["rev_week"], s["rev_month"],
        s["rev_ly"], s["rev_diff"],
        s["revpar"], s["dates_booked"],
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log("=" * 56)
    log("BAYSIDE HOUSE -- weekly_report.py starting")

    if len(sys.argv) > 1:
        week_end = date.fromisoformat(sys.argv[1])
        log(f"Week-end date overridden via argument: {week_end}")
    else:
        week_end = date.today() - timedelta(days=1)

    week_start  = week_end - timedelta(days=6)
    month_start = week_end.replace(day=1)
    year_start  = week_end.replace(month=1, day=1)
    ly_end      = week_end   - timedelta(days=365)
    ly_start    = week_start - timedelta(days=365)

    log(f"Fetching data for {week_start} to {week_end} ...")
    client = CloudbedsClient()

    log("  -> Bed count ...")
    n_beds = client.get_bed_count()
    log(f"     {n_beds} beds")

    log("  -> Arrivals this week (check-in date) ...")
    arrivals_week = client.get_arrivals(week_start, week_end)
    log(f"     {len(arrivals_week)} arrivals")
    bookings_week = arrivals_week  # used for summary counts (same as arrivals by design)

    log("  -> Date Booked this week (dateCreated, newest-first early-stop) ...")
    bookings_created = client.get_bookings_created(week_start, week_end)
    log(f"     {len(bookings_created)} bookings created this week")

    CANCEL_S_DB = {"cancelled", "canceled", "no_show"}
    db_confirmed_for_beds = [r for r in bookings_created
                             if str(r.get("status", "")).lower() not in CANCEL_S_DB]
    log(f"  -> Fetching real bed/room counts for {len(db_confirmed_for_beds)} "
        f"Date Booked reservations (one Cloudbeds call each) ...")
    db_beds_actual = 0
    for r in db_confirmed_for_beds:
        rid = r.get("reservationID")
        if not rid:
            continue
        try:
            db_beds_actual += client.get_reservation_bed_count(rid)
        except Exception as exc:
            log(f"     WARNING: bed count fetch failed for reservation {rid} -- {exc}")
    log(f"     Total beds (Date Booked): {db_beds_actual}")

    log(f"  -> Nightly occupancy + revenue YTD ({year_start} to {week_end}) ...")
    rooms_sold = client.get_rooms_sold(year_start, week_end)
    rs_by_date = {r["date"]: r for r in rooms_sold}
    log(f"     {len(rooms_sold)} days fetched")

    # Fetch full previous year for Revenue tab 2025 column + LY week comparison
    ly_year_start = date(ly_end.year, 1, 1)
    ly_year_end   = date(ly_end.year, 12, 31)
    log(f"  -> Revenue {ly_end.year} full year (for Revenue tab + LY comparison) ...")
    rooms_sold_ly_full = client.get_rooms_sold(ly_year_start, ly_year_end)
    log(f"     {len(rooms_sold_ly_full)} days fetched (LY full year)")
    rs_ly_by_date = {r["date"]: r for r in rooms_sold_ly_full}
    ly_nights = [ly_start + timedelta(days=i) for i in range(7)]
    rooms_sold_ly = [rs_ly_by_date[d] for d in ly_nights if d in rs_ly_by_date]

    def night_count(d: date) -> int:
        return rs_by_date.get(d, {}).get("accommodations_booked", 0)

    week_nights  = [week_start + timedelta(days=i) for i in range(7)]
    nightly_week = [night_count(d) for d in week_nights]
    for d, c in zip(week_nights, nightly_week):
        log(f"     {d}: {c} beds")

    month_nights  = [month_start + timedelta(days=i) for i in range((week_end - month_start).days + 1)]
    nightly_month = [night_count(d) for d in month_nights]

    ytd_nights  = [year_start + timedelta(days=i) for i in range((week_end - year_start).days + 1)]
    nightly_ytd = [night_count(d) for d in ytd_nights]

    people_eow    = night_count(week_end)
    di_rev_week   = sum(rs_by_date.get(d, {}).get("revenue", 0.0) for d in week_nights)
    di_rev_month  = sum(rs_by_date.get(d, {}).get("revenue", 0.0) for d in month_nights)
    di_rev_ytd    = sum(r.get("revenue", 0.0) for r in rooms_sold)
    di_rev_ly     = sum(r.get("revenue", 0.0) for r in rooms_sold_ly)
    log(f"  -> People in house at week end: {people_eow}")
    log(f"  -> Revenue this week: ${di_rev_week:,.2f}  |  MTD: ${di_rev_month:,.2f}  |  LY: ${di_rev_ly:,.2f}")

    # Monthly revenue totals for Revenue tab (2026 YTD + full 2025)
    monthly_revenues: dict[tuple, float] = {}
    for d, r in rs_by_date.items():
        key = (d.year, d.month)
        monthly_revenues[key] = monthly_revenues.get(key, 0.0) + r.get("revenue", 0.0)
    for r in rooms_sold_ly_full:
        d = r["date"]
        key = (d.year, d.month)
        monthly_revenues[key] = monthly_revenues.get(key, 0.0) + r.get("revenue", 0.0)

    # Calculate occupancy for all months YTD (for Occupancy tab updates)
    monthly_occs: dict[str, float] = {}
    for month_num in range(1, week_end.month + 1):
        month_start_check = date(week_end.year, month_num, 1)
        if month_num == week_end.month:
            month_end_check = week_end
        else:
            month_end_check = date(week_end.year, month_num + 1, 1) - timedelta(days=1)

        month_nights_check = []
        for d in range((month_end_check - month_start_check).days + 1):
            check_date = month_start_check + timedelta(days=d)
            occupied = rs_by_date.get(check_date, {}).get("accommodations_booked", 0)
            month_nights_check.append(occupied)

        if month_nights_check and n_beds:
            occ_pct = sum(month_nights_check) / (n_beds * len(month_nights_check)) * 100
            from sheets_client import MONTHS
            month_name = MONTHS[month_num - 1]
            monthly_occs[month_name] = occ_pct

    stats = crunch(
        arrivals_week, bookings_week, bookings_created,
        people_eow, nightly_week, nightly_month, nightly_ytd,
        di_rev_week, di_rev_month, di_rev_ly, di_rev_ytd,
        n_beds, week_start, week_end,
        db_beds_actual=db_beds_actual,
    )

    print_report(stats, week_start, week_end)

    # -- Google Analytics 4 --------------------------------------------------
    ga4_data = None
    if os.environ.get("GA4_PROPERTY_ID"):
        log("  -> Google Analytics 4 (website traffic) ...")
        try:
            ga4 = GA4Client()
            ga4_data = ga4.get_weekly_traffic(week_start, week_end)
            log(f"     Sessions: {ga4_data['total_sessions']}  "
                f"Users: {ga4_data['total_users']}  "
                f"Page Views: {ga4_data['total_pageviews']}")
            demographics = ga4.get_demographics(week_start, week_end)
            ga4_data.update(demographics)
            top_c = demographics.get("top_countries", [])
            log(f"     Top country: {top_c[0][0] if top_c else 'n/a'}")
        except Exception as exc:
            log(f"     WARNING: GA4 fetch failed -- {exc}")
            log("     Website Analytics tab will be skipped this run.")
    else:
        log("  -> GA4_PROPERTY_ID not set -- skipping Google Analytics fetch.")

    # Platform review scores (update these manually or via API)
    # Scales: Google /5, Booking.com /10, Hostelworld /10, Expedia /5
    platform_reviews = {
        "google": 4.0,
        "booking": 7.5,
        "hostelworld": 8.0,
        "expedia": 7.4,
    }

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if sheet_id:
        log("Writing to Google Sheet ...")
        sheets_client.write_report(stats, week_end, monthly_revenues, sheet_id,
                                   ga4_data=ga4_data, monthly_occs=monthly_occs,
                                   platform_reviews=platform_reviews)
    else:
        log("GOOGLE_SHEET_ID not set -- skipping sheet write.")

    # -- Operating review (reads the numbers, tells the story) ---------------
    # If ANTHROPIC_API_KEY is set, Claude writes the story automatically.
    # Otherwise we save a ready-to-paste digest for the Claude Pro chat (free).
    insights_text = None
    try:
        import insights as insights_mod
        svc = sheets_client._build_service() if sheet_id else None

        if os.environ.get("ANTHROPIC_API_KEY"):
            log("  -> Generating operating-review insights (API) ...")
            insights_text = insights_mod.generate_insights(
                stats, week_start, week_end, nightly_week,
                service=svc, sheet_id=sheet_id, log=log,
            )
            if insights_text:
                log("")
                log("  ===== THIS WEEK'S STORY =====")
                for line in insights_text.splitlines():
                    log("  " + line)
                log("  =============================")
                log("")
        else:
            log("  -> Saving paste-ready digest for Claude chat (no API key) ...")
            digest = insights_mod.build_paste_digest(
                stats, week_start, week_end, nightly_week,
                service=svc, sheet_id=sheet_id,
            )
            script_dir = os.path.dirname(os.path.abspath(__file__))
            digest_path = os.path.join(
                script_dir, f"Weekly_Story_Prompt_{week_end.isoformat()}.txt")
            with open(digest_path, "w", encoding="utf-8") as f:
                f.write(digest)
            log(f"     Paste this file into Claude chat: {digest_path}")
    except Exception as exc:
        log(f"     WARNING: insights step failed -- {exc}")

    log("Generating HTML dashboard ...")
    try:
        html_path = html_report.generate_html_report(
            stats, week_start, week_end, nightly_week,
            ga4_data=ga4_data, insights=insights_text,
        )
        log(f"  HTML report saved: {html_path}")
    except Exception as exc:
        log(f"  WARNING: HTML report generation failed -- {exc}")

    if sheet_id:
        log("Regenerating live dashboard (Bayside_Dashboard.html) ...")
        try:
            import build_dashboard
            build_dashboard.build(sheet_id=sheet_id, log=log)
        except Exception as exc:
            log(f"  WARNING: dashboard build failed -- {exc}")
        else:
            log("Publishing dashboard to GitHub (Vercel auto-deploys on push) ...")
            try:
                import subprocess
                script_dir = os.path.dirname(os.path.abspath(__file__))
                subprocess.run(["git", "add", "Bayside_Dashboard.html"],
                                cwd=script_dir, check=True)
                diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=script_dir)
                if diff.returncode != 0:
                    commit_msg = f"Weekly dashboard update -- week ending {week_end.isoformat()}"
                    subprocess.run(["git", "commit", "-m", commit_msg], cwd=script_dir, check=True)
                    subprocess.run(["git", "push"], cwd=script_dir, check=True)
                    log("  Pushed to GitHub -- Vercel will redeploy automatically.")
                else:
                    log("  No changes to publish (dashboard unchanged).")
            except Exception as exc:
                log(f"  WARNING: git publish failed -- {exc}")
    else:
        log("GOOGLE_SHEET_ID not set -- skipping live dashboard build/publish.")

    log("Done. weekly_report.py finished successfully.")
    log("=" * 56)


if __name__ == "__main__":
    main()
