#!/usr/bin/env python
"""
Show or update the platform review scores and counts the dashboard reads.

Google updates itself every week from the Places API. Booking.com, Hostelworld
and Expedia have no self-serve API for a property (see reviews_client), so use
this when one of their numbers changes -- no code edit required.

    python update_platform_reviews.py
        Show what the dashboard is currently using, and where it came from.

    python update_platform_reviews.py --booking 7.8 --booking-count 519
        Record new values. Anything not passed carries forward unchanged.

Values are appended as a new dated row in the Platform Reviews tab, so the
history is kept. Run build_dashboard.py afterwards to publish them.
"""
import argparse
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

import build_dashboard
import reviews_client
import sheets_client

SCALES = reviews_client.SCALES


def _describe(platform, values):
    """One aligned line: 'booking      7.7/10     511 reviews'."""
    rating = "--" if values["rating"] is None else f"{values['rating']:.1f}"
    count = "--" if values["count"] is None else f"{values['count']:,}"
    return f"{platform:<12} {rating:>4}/{SCALES[platform]:<3} {count:>7} reviews"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    for platform in reviews_client.PLATFORMS:
        parser.add_argument(f"--{platform}", type=float,
                            help=f"{platform} rating out of {SCALES[platform]}")
        parser.add_argument(f"--{platform}-count", type=int,
                            help=f"{platform} number of reviews")
    args = parser.parse_args()

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise SystemExit("GOOGLE_SHEET_ID is not set -- check your .env")

    service = sheets_client._build_service()
    current = build_dashboard.fetch_platform_reviews(service, sheet_id) or {}
    current = {p: current.get(p) or {"rating": None, "count": None}
               for p in reviews_client.PLATFORMS}

    updates = {p: {"rating": getattr(args, p), "count": getattr(args, f"{p}_count")}
               for p in reviews_client.PLATFORMS}
    changed = {p: v for p, v in updates.items()
               if v["rating"] is not None or v["count"] is not None}

    if not changed:
        print("Current platform reviews (what the dashboard shows):\n")
        for platform in reviews_client.PLATFORMS:
            source = "live from Places API" if platform == "google" else "entered by hand"
            print(f"  {_describe(platform, current[platform])}   ({source})")
        print("\nPass e.g. --booking 7.8 --booking-count 519 to record new values.")
        return

    merged = {}
    for platform in reviews_client.PLATFORMS:
        merged[platform] = {
            key: updates[platform][key] if updates[platform][key] is not None
            else current[platform][key]
            for key in ("rating", "count")
        }

    print("Recording:\n")
    for platform in reviews_client.PLATFORMS:
        mark = "  <-- changed" if current[platform] != merged[platform] else ""
        print(f"  {_describe(platform, merged[platform])}{mark}")

    plat_sid = sheets_client._ensure_tab(
        service, sheet_id, sheets_client.PLAT_TAB,
        sheets_client._get_tabs(service, sheet_id))
    sheets_client.append_platform_reviews(service, sheet_id, plat_sid, merged, date.today())
    print("\nWritten to the Platform Reviews tab.")
    print("Run  python build_dashboard.py  to publish it to the dashboard.")


if __name__ == "__main__":
    main()
