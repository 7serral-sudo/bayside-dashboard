#!/usr/bin/env python
"""
Re-test whether each platform's numbers are reachable from an unattended run.

Booking.com and Expedia block plain requests today (202 and 429 bot
challenges). That could change -- either because they relax it, or because
Bayside gets Expedia Partner Central API credentials. Rather than anyone
re-deriving the answer from scratch, run this:

    python probe_review_sources.py

It reports what each source returns right now, so the conclusions in
reviews_client's docstring can be checked rather than trusted.
"""
import re

import requests
from dotenv import load_dotenv

load_dotenv()

import reviews_client

PUBLIC_PAGES = {
    "booking": "https://www.booking.com/hotel/au/bayside-house-st-kilda.html",
    "expedia": "https://www.expedia.com.au/Melbourne-Hotels-Bayside-House.h77802432.Hotel-Information",
}
RATING_MARKERS = ("ratingValue", "reviewCount", "reviewScore", "review_score")


def probe_page(name, url):
    try:
        resp = requests.get(url, headers=reviews_client.BROWSER_HEADERS,
                            timeout=reviews_client.REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        print(f"  {name:<12} unreachable -- {type(exc).__name__}: {exc}")
        return

    markers = {m: len(re.findall(m, resp.text)) for m in RATING_MARKERS}
    markers = {k: v for k, v in markers.items() if v}
    verdict = "READABLE -- worth wiring up" if markers else "blocked / no rating in HTML"
    print(f"  {name:<12} HTTP {resp.status_code}  {len(resp.text):>8,}B  "
          f"{markers or '{}'}  {verdict}")


def main():
    print("Live fetchers (what the weekly run actually uses):")
    for name, fetcher in (("google", reviews_client.fetch_google),
                          ("hostelworld", reviews_client.fetch_hostelworld),
                          ("booking", reviews_client.fetch_booking),
                          ("expedia", reviews_client.fetch_expedia)):
        result = fetcher()
        print(f"     {name}: {'OK ' + str(result) if result else 'unavailable -- see log above'}")

    print("\nPublic pages that block direct reads (hence SerpApi):")
    for name, url in PUBLIC_PAGES.items():
        probe_page(name, url)

    print("\nIf a blocked source now shows rating markers, add a fetcher for it in\n"
          "reviews_client.py and update that module's docstring.")


if __name__ == "__main__":
    main()
