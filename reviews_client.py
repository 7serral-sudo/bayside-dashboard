"""
Platform review scores and counts for the dashboard's "Platform reviews" row.

WHAT CAN AND CANNOT BE AUTOMATED (measured August 2026, not assumed)
--------------------------------------------------------------------
The test that matters is not "is there an API" but "can the unattended weekly
run read it", since nobody is driving a browser at 8am on a Monday.

Google       LIVE, via API. Places API (New) returns `rating` and
             `userRatingCount` from a plain API key. Needs
             GOOGLE_PLACES_API_KEY and GOOGLE_PLACE_ID in .env.

Hostelworld  LIVE, via the public page. The partner API is gated, but the
             property page publishes schema.org JSON-LD carrying ratingValue
             and reviewCount, and a plain request gets HTTP 200. Watch the
             trap in _aggregate_rating_for().

Booking.com  BLOCKED. Neither API is open to a property (Connectivity
             "Property Scores" is for certified providers and returns
             content-quality scores, not the guest score; the Demand API's
             reviews endpoint needs affiliate credentials). The public page
             answers HTTP 202 with a ~4KB bot interstitial, with browser
             headers or without. Manual.

Expedia      BLOCKED FOR NOW. The Lodging Supply GraphQL API exposes an
             `aggregatedReviews` query to lodging partners -- genuinely
             reachable once an Expedia Partner Central API integration is
             provisioned. Add EXPEDIA_* credentials and fill in
             fetch_expedia(). The public page answers HTTP 429. Manual.

Cloudbeds aggregates all four in its Reputation dashboard but does not expose
them on the public API, so it is not a shortcut here.

Run probe_review_sources.py to re-test all four; the blocked pair fail loudly
there rather than silently inside a weekly run.

Everything this module cannot fetch falls back to the last value recorded in
the "Platform Reviews" sheet tab, so the dashboard never silently zeroes out.
"""
import json
import os
import re

import requests

REQUEST_TIMEOUT = (5, 25)          # (connect, read) seconds -- never hang the weekly run

PLACES_URL = "https://places.googleapis.com/v1/places/{place_id}"
HOSTELWORLD_URL = "https://www.hostelworld.com/hostels/p/313555/bayside-house/"

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

# Platforms in dashboard order, with the denominator each score is shown out of.
PLATFORMS = ("google", "booking", "hostelworld", "expedia")
SCALES = {"google": 5, "booking": 10, "hostelworld": 10, "expedia": 10}


def fetch_google(log=print):
    """Rating and review count from the Google Places API.

    Returns {"rating": float, "count": int} or None if unconfigured/unreachable.
    """
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    place_id = os.environ.get("GOOGLE_PLACE_ID")
    if not api_key or not place_id:
        log("     Google: GOOGLE_PLACES_API_KEY / GOOGLE_PLACE_ID not set -- skipping.")
        return None

    try:
        resp = requests.get(
            PLACES_URL.format(place_id=place_id),
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "rating,userRatingCount",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log(f"     Google: Places API call failed -- {exc}")
        return None
    except ValueError as exc:
        log(f"     Google: Places API returned non-JSON -- {exc}")
        return None

    rating, count = data.get("rating"), data.get("userRatingCount")
    if rating is None or count is None:
        log(f"     Google: Places API response missing rating/userRatingCount -- {data}")
        return None

    log(f"     Google: {rating:.1f}/5 from {count} reviews (live)")
    return {"rating": float(rating), "count": int(count)}


def _iter_nodes(value):
    """Every dict nested anywhere inside a decoded JSON document."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nodes(child)


def _aggregate_rating_for(html, page_url):
    """Pull schema.org aggregateRating out of a page's JSON-LD.

    Matches on the node whose `url` is the property page. Hostelworld's markup
    also carries an aggregateRating for the Hostelworld *app* (4.2 from ~9k
    ratings), so taking the first one found would silently report the wrong
    number -- hence the url match rather than a blind search.
    """
    for block in re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            document = json.loads(block)
        except ValueError:
            continue
        for node in _iter_nodes(document):
            rating = node.get("aggregateRating")
            if not isinstance(rating, dict):
                continue
            if str(node.get("url", "")).rstrip("/") != page_url.rstrip("/"):
                continue
            value, count = rating.get("ratingValue"), rating.get("reviewCount")
            if value is None or count is None:
                continue
            return float(value), int(count)
    return None


def fetch_hostelworld(log=print):
    """Rating and review count from the public Hostelworld property page.

    Hostelworld's partner API is gated, but the property page publishes the
    same figures as schema.org JSON-LD, which a plain request can read.
    """
    url = os.environ.get("HOSTELWORLD_URL", HOSTELWORLD_URL)
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"     Hostelworld: page fetch failed -- {exc}")
        return None

    found = _aggregate_rating_for(resp.text, url)
    if not found:
        log("     Hostelworld: no aggregateRating for the property in the page's "
            "JSON-LD -- their markup may have changed.")
        return None

    rating, count = found
    log(f"     Hostelworld: {rating:.1f}/10 from {count} reviews (live)")
    return {"rating": rating, "count": count}


def fetch_expedia(log=print):
    """Placeholder for the Expedia Lodging Supply GraphQL `aggregatedReviews`
    query. Reachable only once Expedia Partner Central API credentials exist --
    see the module docstring."""
    if not os.environ.get("EXPEDIA_CLIENT_ID"):
        return None
    log("     Expedia: credentials present but fetch_expedia() is not implemented yet.")
    return None


def fetch_all(log=print):
    """Fetch every platform that has a usable API.

    Returns {platform: {"rating": float, "count": int}} containing only the
    platforms that actually came back. Callers fill the gaps from the sheet.
    """
    fetched = {}
    for name, fetcher in (("google", fetch_google),
                          ("hostelworld", fetch_hostelworld),
                          ("expedia", fetch_expedia)):
        result = fetcher(log=log)
        if result:
            fetched[name] = result
    return fetched


def merge_with_last_known(fetched, last_known, log=print):
    """Combine live values with the last ones recorded in the sheet.

    Live wins. Anything not fetched carries its previous value forward so the
    row stays complete, and the log says which platforms are riding on a manual
    figure -- that is the cue to go and re-check them.
    """
    merged, carried = {}, []
    for platform in PLATFORMS:
        live = fetched.get(platform)
        if live:
            merged[platform] = live
            continue
        previous = (last_known or {}).get(platform) or {}
        merged[platform] = {"rating": previous.get("rating"), "count": previous.get("count")}
        if merged[platform]["rating"] is not None:
            carried.append(platform)

    if carried:
        log(f"     Carried forward (no API -- update by hand): {', '.join(carried)}")
    missing = [p for p in PLATFORMS if merged[p]["rating"] is None]
    if missing:
        log(f"     WARNING: no rating on record at all for: {', '.join(missing)}")
    return merged
