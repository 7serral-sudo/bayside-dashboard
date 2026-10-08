"""
payments_compare.py
Monthly Stripe vs Cloudbeds comparison for the dashboard's "Stripe vs
Cloudbeds" section.

Cloudbeds side (computed by the weekly run): every card/cash payment and
refund by the month it was posted, plus accommodation revenue for the same
month. Stripe side: typed in by hand on the "Stripe vs Cloudbeds" sheet tab
(column B) -- nothing here can read Stripe -- and carried over on every
rewrite so the weekly run never overwrites it.
"""
import os
from collections import defaultdict
from datetime import date, timedelta

import sheets_client

TAB = "Stripe vs Cloudbeds"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HEADER = ["Month", "Stripe (type in)", "Cloudbeds card payments", "Card refunds",
          "Cloudbeds card net", "Cash", "Cloudbeds revenue"]

# First-run seed for the hand-entered Stripe column (Jan-Sep 2026, whole dollars).
STRIPE_SEED = {1: 79758, 2: 63700, 3: 85165, 4: 79199, 5: 64806,
               6: 53684, 7: 55989, 8: 48412, 9: 72878}


def fetch_monthly_payments(client, year_start: date, date_to: date) -> dict:
    """{month_number: {"card": x, "card_refund": x, "cash": x, "cash_refund": x}}
    for payments/refunds posted in [year_start, date_to]. Property-wide
    getTransactions, filtered by modified time (the only date filter the API
    honours) and bucketed by each transaction's own posting date."""
    rows = {}
    d = year_start
    while d <= date_to:
        d2 = min(d + timedelta(days=6), date_to)
        page = 1
        while True:
            r = client._get("getTransactions", {
                "modifiedFrom": f"{d} 00:00:00", "modifiedTo": f"{d2} 23:59:59",
                "pageSize": 1000, "pageNumber": page})
            data = r.get("data", [])
            for t in data:
                if t.get("transactionCategory") in ("payment", "refund") and not t.get("isDeleted"):
                    rows[t["transactionID"]] = t
            if page * 1000 >= (r.get("total") or 0) or not data:
                break
            page += 1
        d = d2 + timedelta(days=1)

    out = defaultdict(lambda: {"card": 0.0, "card_refund": 0.0, "cash": 0.0, "cash_refund": 0.0})
    for t in rows.values():
        when = (t.get("transactionDateTime") or "")[:10]
        if not when.startswith(str(year_start.year)):
            continue
        month = int(when[5:7])
        method = "cash" if t.get("originID") == "cash" else "card"
        amt = float(t.get("amount") or 0)
        if t["transactionCategory"] == "payment":
            out[month][method] += amt
        else:
            out[month][method + "_refund"] += abs(amt)
    return dict(out)


def write_tab(monthly: dict, revenue_by_month: dict, year: int, last_month: int, log=print):
    """Rewrites the tab, keeping whatever is already typed in the Stripe column."""
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    service = sheets_client._build_service()
    existing = sheets_client._get_tabs(service, sheet_id)
    first_time = TAB not in existing
    sheets_client._ensure_tab(service, sheet_id, TAB, existing)

    stripe = {}
    if not first_time:
        old = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{TAB}!A2:B13", valueRenderOption="UNFORMATTED_VALUE").execute()
        for r in old.get("values", []):
            if len(r) > 1 and r[1] not in ("", None) and r[0] in MONTHS:
                stripe[MONTHS.index(r[0]) + 1] = r[1]
    else:
        stripe = dict(STRIPE_SEED)

    rows = []
    for m in range(1, last_month + 1):
        p = monthly.get(m, {"card": 0.0, "card_refund": 0.0, "cash": 0.0})
        rows.append([MONTHS[m - 1], stripe.get(m, ""),
                     round(p["card"], 2), round(p["card_refund"], 2),
                     round(p["card"] - p["card_refund"], 2), round(p["cash"], 2),
                     round(revenue_by_month.get((year, m), 0.0), 2)])

    sheets_client._clear_tab(service, sheet_id, TAB)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"{TAB}!A1", valueInputOption="RAW",
        body={"values": [HEADER] + rows}).execute()
    log(f"  -> {TAB}: {len(rows)} months written (Stripe column kept as typed)")
