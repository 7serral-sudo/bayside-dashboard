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

Booking.com  LIVE, via SerpApi. Neither of its own APIs is open to a property
             (Connectivity "Property Scores" is for certified providers and
             returns content-quality scores, not the guest score; the Demand
             API's reviews endpoint needs affiliate credentials), and the
             public page answers HTTP 202 with a ~4KB bot interstitial with or
             without browser headers. Google's results carry the figure
             though, so we read it from there. Needs SERPAPI_KEY.

Expedia      LIVE, via SerpApi -- its public page answers HTTP 429. The
             Lodging Supply GraphQL API's `aggregatedReviews` query would be
             the better source and is reachable once an Expedia Partner
             Central integration is provisioned; swap fetch_expedia() over if
             that happens.

Cloudbeds aggregates all four in its Reputation dashboard but does not expose
them on the public API, so it is not a shortcut here.

WHY SERPAPI RATHER THAN SCRAPING GOOGLE DIRECTLY
------------------------------------------------
A single Google search returns all four platforms' figures on one page, but
Google will not serve it to a scheduled job: plain requests to /search gets
HTTP 200 carrying only a redirect shell reading "Please click here if you are
not redirected", and a clean automated browser gets the "unusual traffic" bot
check. It works in a signed-in human session, which the Monday run is not.
SerpApi does that fetch and hands back JSON.

Its free tier recurs monthly and needs no card; this costs one call per
platform per week, so it stays inside the free allowance permanently. Serper
was the alternative, rejected because its 2,500 credits are a one-time grant
rather than a recurring allowance -- wrong shape for a job meant to run for
years unattended.

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
SERPAPI_URL = "https://serpapi.com/search"

# Which domain a platform's own listing lives on, for picking the right result.
SERP_DOMAINS = {"booking": "booking.com", "expedia": "expedia."}
# The search also returns other St Kilda hostels and Expedia category pages, so
# a matching domain isn't enough -- the link must name this property.
PROPERTY_URL_TOKENS = ("bayside-house", "bayside_house", "bayside-house.h77802432")

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


def _serp_detected_extensions(result):
    """rating/reviews out of a SerpApi organic result.

    They live under rich_snippet.top.detected_extensions, but the same pair can
    land under `bottom` depending on how Google laid the result out, so check
    both rather than assuming.
    """
    snippet = result.get("rich_snippet") or {}
    for section in ("top", "bottom"):
        extensions = (snippet.get(section) or {}).get("detected_extensions") or {}
        rating, reviews = extensions.get("rating"), extensions.get("reviews")
        if rating is not None and reviews is not None:
            return float(rating), int(reviews)
    return None


def fetch_via_serp(platform, log=print):
    """Rating and count for one platform, read off Google's own search results.

    Booking.com and Expedia both block direct automated reads of their property
    pages, but Google surfaces the same figures in its search results. SerpApi
    returns that page as JSON, which sidesteps the bot check a scheduled run
    would otherwise hit. Needs SERPAPI_KEY.
    """
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return None

    domain = SERP_DOMAINS[platform]
    try:
        resp = requests.get(
            SERPAPI_URL,
            params={"engine": "google", "q": f"bayside house {platform} reviews",
                    "google_domain": "google.com.au", "gl": "au", "hl": "en",
                    "num": 10, "api_key": api_key},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        log(f"     {platform}: SerpApi call failed -- {exc}")
        return None
    except ValueError as exc:
        log(f"     {platform}: SerpApi returned non-JSON -- {exc}")
        return None

    if payload.get("error"):
        log(f"     {platform}: SerpApi error -- {payload['error']}")
        return None

    # Only trust a result that is actually the property's page on that platform;
    # the same search also surfaces Tripadvisor, Skyscanner and other hostels.
    for result in payload.get("organic_results") or []:
        link = str(result.get("link", ""))
        if domain not in link:
            continue
        if not any(token in link.lower() for token in PROPERTY_URL_TOKENS):
            continue
        found = _serp_detected_extensions(result)
        if found:
            rating, count = found
            log(f"     {platform}: {rating:.1f}/{SCALES[platform]} from {count} "
                f"reviews (via SerpApi)")
            return {"rating": rating, "count": count}

    log(f"     {platform}: no rating found in the SerpApi result for {domain}.")
    return None


def fetch_booking(log=print):
    return fetch_via_serp("booking", log=log)


def fetch_expedia(log=print):
    """Expedia via Google's results.

    The Lodging Supply GraphQL API's `aggregatedReviews` query would be the
    better source, but it needs Expedia Partner Central credentials -- see the
    module docstring.
    """
    return fetch_via_serp("expedia", log=log)


def fetch_all(log=print):
    """Fetch every platform that has a usable API.

    Returns {platform: {"rating": float, "count": int}} containing only the
    platforms that actually came back. Callers fill the gaps from the sheet.
    """
    fetched = {}
    for name, fetcher in (("google", fetch_google),
                          ("hostelworld", fetch_hostelworld),
                          ("booking", fetch_booking),
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
