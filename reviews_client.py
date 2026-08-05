"""
Platform review scores and counts for the dashboard's "Platform reviews" row.

WHAT CAN AND CANNOT BE AUTOMATED (checked August 2026)
------------------------------------------------------
Google       LIVE. Places API (New) returns `rating` and `userRatingCount` for
             any place from a plain API key. Needs GOOGLE_PLACES_API_KEY and
             GOOGLE_PLACE_ID in .env.

Booking.com  NO SELF-SERVE API. Two exist and neither is open to a property:
             the Connectivity "Property Scores API" is for certified
             connectivity providers (and returns content-quality scores, not
             the public guest score), and the Demand API's accommodation
             reviews endpoint needs affiliate-partner credentials.

Hostelworld  NO SELF-SERVE API. partner-api.hostelworld.com is gated behind
             their booking-partner/affiliate programme.

Expedia      NOT WITHOUT ONBOARDING. The Lodging Supply GraphQL API does expose
             an `aggregatedReviews` query to lodging partners, so this one is
             genuinely reachable -- but only after an Expedia Partner Central
             API integration is provisioned. Add EXPEDIA_* credentials and
             wire fetch_expedia() when that happens.

Cloudbeds aggregates all four in its Reputation dashboard but does not expose
them on the public API, so it is not a shortcut here.

Everything this module cannot fetch falls back to the last value recorded in
the "Platform Reviews" sheet tab, so the dashboard never silently zeroes out.
"""
import os

import requests

REQUEST_TIMEOUT = (5, 15)          # (connect, read) seconds -- never hang the weekly run

PLACES_URL = "https://places.googleapis.com/v1/places/{place_id}"

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
    for name, fetcher in (("google", fetch_google), ("expedia", fetch_expedia)):
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
