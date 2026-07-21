"""
fetch_2025_reference.py
One-time (occasionally re-run) fetch of full-year 2025 data used for
year-over-year comparisons on the dashboard:
  - daily accommodations_booked + revenue (from Data Insights, reliable)
  - monthly check-in counts (from get_arrivals, chunked one-week-at-a-time
    to stay clear of Cloudbeds' broken offset pagination on this account)

2025 is a closed year -- this data won't change except for rare very-late
modifications, so it's cached to data_2025_reference.json rather than
re-fetched on every dashboard build.
"""
import json
import os
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from cloudbeds_client import CloudbedsClient

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_2025_reference.json")


def main():
    client = CloudbedsClient()

    print("Fetching 2025 daily occupancy + revenue (Data Insights)...")
    rooms = client.get_rooms_sold(date(2025, 1, 1), date(2025, 12, 31))
    daily = {r["date"].isoformat(): {"accommodations_booked": r["accommodations_booked"],
                                      "revenue": r["revenue"]} for r in rooms}
    print(f"  {len(daily)} days fetched")

    print("Fetching 2025 check-ins by month (52 weekly-chunked calls, reliable)...")
    monthly_checkins = {m: 0 for m in range(1, 13)}
    chunk_start = date(2025, 1, 1)
    year_end = date(2025, 12, 31)
    CANCEL_S = {"cancelled", "canceled", "no_show"}
    while chunk_start <= year_end:
        chunk_end = min(chunk_start + timedelta(days=6), year_end)
        arrivals = client.get_arrivals(chunk_start, chunk_end)
        for r in arrivals:
            if str(r.get("status", "")).lower() in CANCEL_S:
                continue
            try:
                ci = date.fromisoformat(r["startDate"])
            except (KeyError, ValueError):
                continue
            monthly_checkins[ci.month] = monthly_checkins.get(ci.month, 0) + 1
        chunk_start = chunk_end + timedelta(days=1)
        print(f"  through {chunk_end}: running monthly totals so far updated")

    print("Monthly check-ins 2025:", monthly_checkins)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"daily": daily, "monthly_checkins": monthly_checkins}, f)
    print(f"Saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
