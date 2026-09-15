"""
Cloudbeds API client for Bayside House weekly reporting.
"""
import os
import re
import time
import requests
from collections import defaultdict
from datetime import date, datetime, timedelta

BASE_URL = "https://hotels.cloudbeds.com/api/v1.2"
DATAINSIGHTS_URL = "https://api.cloudbeds.com/datainsights/v1.1"

REQUEST_TIMEOUT = (5, 30)          # (connect, read) seconds -- no call may hang forever
RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # transient -- retry. Everything else fails loud.


class CloudbedsClient:
    def __init__(self):
        self.api_key = os.environ["CLOUDBEDS_API_KEY"]
        self.property_id = os.environ["CLOUDBEDS_PROPERTY_ID"]
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    def _get(self, endpoint: str, params: dict) -> dict:
        p = {**params, "propertyID": self.property_id}
        url = f"{BASE_URL}/{endpoint}"
        last_exc = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=p, timeout=REQUEST_TIMEOUT)
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in RETRYABLE_STATUS:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt * 5))
                time.sleep(wait)
                continue
            resp.raise_for_status()  # 4xx other than 429 fails loud, no retry
            try:
                data = resp.json()
            except ValueError as exc:
                raise RuntimeError(f"{endpoint}: non-JSON response -- {resp.text[:300]!r}") from exc
            if not data.get("success"):
                raise RuntimeError(f"{endpoint}: {data.get('message', data)}")
            return data
        if last_exc:
            raise RuntimeError(f"{endpoint}: network error after 3 retries -- {last_exc}")
        raise RuntimeError(f"{endpoint}: rate/server error (retryable status) after 3 retries")

    def _paginate_reservations(self, params: dict) -> list[dict]:
        all_res = []
        p = {**params, "limit": 100, "offset": 0}
        while True:
            data = self._get("getReservations", p)
            batch = data.get("data", [])
            all_res.extend(batch)
            total = data.get("total", len(all_res))
            p["offset"] += len(batch)
            if p["offset"] >= total or not batch:
                break
        return all_res

    def _paginate_reservations_by_checkin(self, date_from: date, date_to: date,
                                           extra_params: dict | None = None) -> list[dict]:
        """
        Fetch all reservations with check-in date in [date_from, date_to].

        Cloudbeds' getReservations endpoint ignores the offset parameter when
        combined with checkInFrom/checkInTo filters -- every page beyond the
        first silently returns the same rows as page 0 instead of the next
        page (verified directly against the live API: offset=0/100/200 all
        returned identical data). _paginate_reservations' offset loop is
        therefore unsafe for check-in-date-filtered queries whose total
        exceeds 100. Instead, we recursively bisect the date range until each
        leaf query's total fits in a single 100-row page, then merge +
        dedupe by reservationID.
        """
        params = {**(extra_params or {}), "checkInFrom": date_from.isoformat(),
                  "checkInTo": date_to.isoformat(), "limit": 100, "offset": 0}
        data = self._get("getReservations", params)
        batch = data.get("data", [])
        total = data.get("total", len(batch))
        if total <= len(batch) or date_from >= date_to:
            return batch
        mid = date_from + (date_to - date_from) // 2
        left = self._paginate_reservations_by_checkin(date_from, mid, extra_params)
        right = self._paginate_reservations_by_checkin(mid + timedelta(days=1), date_to, extra_params)
        merged = {r["reservationID"]: r for r in left + right}
        return list(merged.values())

    def _paginate_transactions(self, params: dict) -> list[dict]:
        all_txns = []
        p = {**params, "limit": 100, "offset": 0}
        while True:
            data = self._get("getTransactions", p)
            batch = data.get("data", [])
            all_txns.extend(batch)
            total = data.get("total", len(all_txns))
            p["offset"] += len(batch)
            if p["offset"] >= total or not batch:
                break
        return all_txns

    def _paginate_transactions_by_servicedate(self, reservation_id: str, date_from: date,
                                               date_to: date) -> list[dict]:
        """
        Fetch all transactions for one reservation with serviceDate in
        [date_from, date_to], correctly handling reservations with more than
        100 matching transactions.

        getTransactions' offset parameter is silently ignored, same bug as
        getReservations -- verified directly: offset=0/100/200/300 all
        returned identical data for a 1280-transaction reservation. Unlike
        the top-level dateFrom/dateTo (also silently ignored -- verified),
        serviceDateFrom/serviceDateTo DO filter correctly (verified: total
        dropped from 1280 to 189 when narrowed to a 1-month window). So we
        recursively bisect the service-date range until each leaf query's
        total fits in a single 100-row page, same strategy as
        _paginate_reservations_by_checkin.
        """
        params = {"reservationID": reservation_id, "serviceDateFrom": date_from.isoformat(),
                  "serviceDateTo": date_to.isoformat(), "limit": 100, "offset": 0}
        data = self._get("getTransactions", params)
        batch = data.get("data", [])
        total = data.get("total", len(batch))
        if total <= len(batch) or date_from >= date_to:
            return batch
        mid = date_from + (date_to - date_from) // 2
        left = self._paginate_transactions_by_servicedate(reservation_id, date_from, mid)
        right = self._paginate_transactions_by_servicedate(reservation_id, mid + timedelta(days=1), date_to)
        merged = {t["transactionID"]: t for t in left + right}
        return list(merged.values())

    # -- Reservations ---------------------------------------------------------

    def get_arrivals(self, date_from: date, date_to: date) -> list[dict]:
        """All reservations (any status) with check-in date in range."""
        return self._paginate_reservations_by_checkin(date_from, date_to)

    def get_reservations_overlapping(self, date_from: date, date_to: date,
                                      earliest_checkin: date = date(2020, 1, 1)) -> list[dict]:
        """
        All reservations whose stay overlaps [date_from, date_to] -- i.e.
        checked in on/before date_to AND checked out on/after date_from.

        Unlike get_arrivals (check-in date within range), this also catches
        long-termers who checked in before date_from and are still in house
        or checked out partway through the window (verified against a real
        400+-night reservation that get_arrivals silently missed entirely
        because its check-in predated the query window).
        """
        return self._paginate_reservations_by_checkin(
            earliest_checkin, date_to,
            extra_params={"checkOutFrom": date_from.isoformat()},
        )

    def get_bookings_created(self, date_from: date, date_to: date) -> list[dict]:
        """
        Reservations where dateCreated (booking date) falls within [date_from, date_to].

        The Cloudbeds API ignores dateCreated filter params but returns records
        newest-first. We paginate from the top and stop as soon as dateCreated
        drops below date_from — typically 1-2 pages for a weekly window.
        """
        results = []
        params = {"limit": 100, "offset": 0}

        while True:
            data = self._get("getReservations", params)
            batch = data.get("data", [])
            if not batch:
                break

            stop = False
            for r in batch:
                dc_str = r.get("dateCreated", "")
                if not dc_str:
                    continue
                try:
                    dc_date = date.fromisoformat(dc_str[:10])
                except ValueError:
                    continue

                if dc_date > date_to:
                    continue          # newer than window, skip
                if dc_date < date_from:
                    stop = True       # older than window, no point paginating further
                    break
                results.append(r)

            if stop:
                break

            total = data.get("total", 0)
            params["offset"] += len(batch)
            if params["offset"] >= total:
                break

        return results

    def get_reservation_room_charges(self, reservation_id: str, date_from: date, date_to: date) -> list[dict]:
        """
        Per-night room-type + accurate revenue detail for a single
        reservation, for nights with date in [date_from, date_to].

        Combines two Cloudbeds sources, each reliable for only HALF of the
        picture -- verified against two real reservations (a 145-night stay
        on a room later reclassified to a different type, and a 420+-night
        stay on a negotiated weekly rate):

        - Revenue: getReservation's assigned/unassigned dailyRates, scaled by
          the reservation's balanceDetailed subTotal/grandTotal ratio to
          strip tax. dailyRates.rate is tax-INCLUSIVE (verified: sums to
          grandTotal exactly for two real reservations, $26,550.00 and
          $5,316.50) while Cloudbeds' Data Insights revenue report -- the
          trusted, already-in-use source for every other revenue figure in
          this codebase -- is ex-tax (verified: netting GST off a real
          week's charges brought the total from $11,957.78 to exactly
          $11,618.14, matching Data Insights to the cent). The ratio is
          applied uniformly per reservation rather than matched tax
          transaction-by-transaction, because a group/family reservation can
          have several beds sharing the same service date, and matching tax
          lines back to individual beds by date alone is unreliable; GST
          was confirmed to be a flat 10% on every taxed night observed, so a
          reservation-wide ratio and per-transaction removal are
          equivalent. getTransactions' own "rate" category is NOT used for
          revenue at all -- for a negotiated weekly-rate guest it overstated
          the true total by over $10,000 (rate+tax = $37,085 vs an actual
          $26,550 grandTotal/paid), apparently tracking an internal
          rack-rate concept rather than what the guest is actually billed.

        - Room type: getTransactions' `description` field ("Room rate -
          <type>"), parsed text. Frozen at time of charge, so it stays
          accurate even after a room is reclassified to a different type --
          confirmed against a real folio where getReservation's roomTypeID
          retroactively showed the reservation's CURRENT type for 100% of
          its history, including nights billed months before the
          reclassification actually happened.

        Joined by calendar date. A night present in getReservation but
        missing a matching transaction-based type label (e.g. a "rate"
        transaction with an unexpected description) falls back to the
        reservation segment's own roomTypeName rather than being dropped.
        """
        data = self._get("getReservation", {"reservationID": reservation_id}).get("data", {})
        bd = data.get("balanceDetailed", {}) or {}
        grand_total = float(bd.get("grandTotal", 0) or 0)
        sub_total = float(bd.get("subTotal", 0) or 0)
        net_ratio = (sub_total / grand_total) if grand_total else 1.0

        # One entry PER BED PER NIGHT -- deliberately not deduped by date. A
        # group/family reservation can have multiple room/bed segments with
        # a dailyRates entry on the same calendar date (one guest per bed),
        # and each of those is a separate occupied bed that Data Insights'
        # accommodations_booked counts separately. Collapsing by date here
        # previously undercounted such reservations by keeping only the
        # last-seen bed's amount per date -- verified directly: Data
        # Insights reported 65 occupied beds for 2026-08-01 while the
        # date-deduped version only ever surfaced 60 distinct reservations'
        # worth of nights for that day.
        raw_charges = []  # (date, amount, fallback_type_name)
        for entry in data.get("assigned", []) + data.get("unassigned", []):
            fallback_name = entry.get("roomTypeName", "Unknown")
            for dr in entry.get("dailyRates", []):
                try:
                    d = date.fromisoformat(dr["date"])
                except (KeyError, ValueError):
                    continue
                if not (date_from <= d <= date_to):
                    continue
                raw_charges.append((d, float(dr.get("rate", 0) or 0) * net_ratio, fallback_name))

        type_by_date: dict = {}
        charges = self._paginate_transactions_by_servicedate(reservation_id, date_from, date_to)
        prefix = "Room rate - "
        for t in charges:
            if t.get("transactionCategory") != "rate" or t.get("isDeleted"):
                continue
            desc = t.get("description", "") or ""
            if not desc.startswith(prefix):
                continue
            try:
                d = date.fromisoformat(t["serviceDate"])
            except (KeyError, ValueError):
                continue
            type_by_date[d] = desc[len(prefix):].strip()

        nights = []
        for d, amount, fallback_name in raw_charges:
            room_type_name = type_by_date.get(d, fallback_name)
            nights.append({"date": d, "roomTypeName": room_type_name, "amount": amount})
        return nights

    def get_reservation_bed_count(self, reservation_id: str) -> int:
        """
        Number of individual room/bed assignments for a reservation -- matches
        Cloudbeds' "Room Number(s)" column (e.g. "Room 15 - Bed 4, Room 15 - Bed 6,
        Room 15 - Bed 1" = 3 beds), NOT nights x adults. Only available via the
        singular getReservation endpoint -- the bulk getReservations list does not
        include room assignment data.
        """
        data = self._get("getReservation", {"reservationID": reservation_id}).get("data", {})
        return len(data.get("assigned", [])) + len(data.get("unassigned", []))

    def get_long_termers(self, target_date: date, min_nights: int = 28,
                          lookback_weeks: int = 26) -> list[dict]:
        """
        Guests whose TRUE cumulative stay -- chaining together back-to-back/
        zero-gap reservations under the same guest name -- exceeds min_nights
        and spans target_date. Returns one dict per qualifying guest:
        {"name", "start", "end", "nights", "source", "reservation_id"}
        (source/reservation_id are the chain's most recent booking), longest
        stay first.

        Cloudbeds mints a brand new guestID *and* profileID for every single
        booking, even repeat weekly bookings by the same real person (verified
        directly against live data), so there is no reliable ID to link a
        guest's rebookings. Exact guest-name matching is the only available
        signal. A single long reservation is also caught here (a chain of one).

        Seeded from two sources:
        - get_arrivals over the last lookback_weeks, to chain together
          separate back-to-back bookings under the same name into one true
          cumulative stay.
        - get_reservations_overlapping(target_date, target_date), to
          guarantee every guest actually in house on target_date is found
          even if their (single) reservation's arrival happened before the
          lookback window and they've had no rebooking event since -- this
          shipped once as a real miss: a guest checked in since June 2025
          (455 nights by September 2026) was invisible to the arrivals-only
          scan because nothing about their stay produced an "arrival" inside
          the most recent 26 weeks.

        Chains still get undercounted if they run longer than the lookback
        window AND involve more than one booking (a rebooking further back
        than lookback_weeks won't be found) -- 26 weeks (~6 months) covers
        everything seen in practice with real headroom, and the overlap
        query is what actually closes the single-long-reservation gap.
        """
        CANCEL_S = {"cancelled", "canceled", "no_show"}
        by_name: dict[str, list[tuple[date, date, str, str]]] = defaultdict(list)

        def _add(r):
            if str(r.get("status", "")).lower() in CANCEL_S:
                return
            try:
                ci = date.fromisoformat(r["startDate"])
                co = date.fromisoformat(r["endDate"])
            except (KeyError, ValueError):
                return
            name = (r.get("guestName") or "").strip()
            if name:
                by_name[name].append((ci, co, r.get("sourceName") or "", r.get("reservationID") or ""))

        for r in self.get_reservations_overlapping(target_date, target_date):
            _add(r)

        chunk_end = target_date
        for _ in range(lookback_weeks):
            chunk_start = chunk_end - timedelta(days=6)
            for r in self.get_arrivals(chunk_start, chunk_end):
                _add(r)
            chunk_end = chunk_start - timedelta(days=1)

        results = []
        for name, stays in by_name.items():
            # sort/dedupe on (start, end) only -- source/reservationID just ride
            # along with whichever row happens to survive the dedupe for that pair
            stays = sorted({(ci, co): (ci, co, src, rid) for ci, co, src, rid in stays}.values())
            if not stays:
                continue
            chains = [[stays[0]]]
            for s in stays[1:]:
                last_checkout = chains[-1][-1][1]
                if s[0] <= last_checkout:  # back-to-back or overlapping
                    chains[-1].append(s)
                else:
                    chains.append([s])
            for chain in chains:
                c_start, c_end = chain[0][0], chain[-1][1]
                nights = (c_end - c_start).days
                if c_start <= target_date <= c_end and nights > min_nights:
                    last = chain[-1]
                    results.append({"name": name, "start": c_start, "end": c_end, "nights": nights,
                                     "source": last[2], "reservation_id": last[3]})

        results.sort(key=lambda r: -r["nights"])
        return results

    def get_long_termers_count(self, target_date: date, min_nights: int = 28,
                                lookback_weeks: int = 26) -> int:
        """Thin wrapper over get_long_termers() for callers that only need
        the count (weekly_report.py's "Long-termers in house" KPI)."""
        return len(self.get_long_termers(target_date, min_nights, lookback_weeks))

    def get_reservation_notes(self, reservation_id: str) -> list[dict]:
        return self._get("getReservationNotes", {"reservationID": reservation_id}).get("data", [])

    def get_payments(self, reservation_id: str) -> list[dict]:
        """Non-deleted payment-category transactions for a reservation,
        oldest first. Each carries transactionDateTime, amount, cardType --
        no cardID/last-4, so a same-typed card swap (e.g. Mastercard to
        Mastercard) is invisible here; only a card TYPE change is detectable."""
        txns = self._get("getTransactions", {"reservationID": reservation_id}).get("data", [])
        payments = [t for t in txns
                    if t.get("transactionCategory") == "payment" and not t.get("isDeleted")]
        payments.sort(key=lambda t: t.get("transactionDateTime", ""))
        return payments

    # Card-type inference can't tell two cards of the SAME type apart (e.g. a
    # guest's own Mastercard vs the OTA's virtual Mastercard both show up
    # identically in getTransactions -- confirmed live with Cole Woods, who
    # had $835.28 on his real Mastercard and $27.26 on Booking.com's virtual
    # Mastercard, invisible to the automatic switch-date logic below). For
    # those cases staff can check Cloudbeds' Credit Cards tab (which DOES
    # show cardholder name / virtual-card flag, unlike the API) and write the
    # confirmed number straight into the reservation note, e.g.:
    #   Extend Direct $835.28
    #   Extend Direct $835.28 from 18 Aug 2026
    # -- overriding the inference entirely once present.
    _OVERRIDE_RE = re.compile(
        r"extend direct\D*\$\s?([\d,]+(?:\.\d{1,2})?)"
        r"(?:\D*?\bfrom\b\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}))?",
        re.IGNORECASE,
    )

    @staticmethod
    def _parse_override_date(s: str):
        for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def find_direct_conversions(self, target_date: date, min_nights: int = 28,
                                 lookback_weeks: int = 26,
                                 extend_tag: str = "extend direct") -> list[dict]:
        """Long-termers whose reservation notes contain `extend_tag` (staff
        manually flagging "we now bill this guest directly instead of
        through the OTA"), with a best-effort switch date and the revenue
        collected since that date -- money currently counted as
        Booking.com/HostelWorld/etc. revenue in channel reporting that is
        actually direct.

        Switch date/amount, in priority order:
        1. A manual override in the note itself (see _OVERRIDE_RE above) --
           authoritative, since staff typed it after checking Cloudbeds
           directly for cases the API can't see.
        2. Every payment carries a `userName` -- "SYSTEM" marks the OTA's
           own automatic deposit collection (its virtual card, always
           tagged "Deposit" too); any other userName means a staff member
           ran that charge by hand, which only happens once they've taken
           the guest over for direct billing. So: exclude SYSTEM payments,
           count everything else as direct. This is NOT the same as "skip
           the first payment" -- confirmed live that several guests (Sion
           John Shearer, Gabriele Manfrinato, Josephine Wiederspahn, Bouchet
           Nicolas, PIN HSUAN HUANG) never had an automatic SYSTEM deposit
           at all, so their very first payment was already staff-run and
           fully direct; "skip payment #1" wrongly excluded it for them.
           Doesn't depend on card type either (unlike the original logic),
           so it isn't fooled by a guest whose own card happens to share a
           type with the OTA's virtual card (e.g. Cole Woods).
        3. If every payment so far is a SYSTEM deposit (nothing staff-run
           yet), the guest has been tagged but nothing's actually been
           charged directly so far -- $0 reclaimed, switch date
           approximated as the tag date.

        Returns one dict per tagged guest: {"name", "source", "nights",
        "checkout", "switch_date", "switch_date_is_exact", "manual_override",
        "revenue_since_switch", "total_paid"}, largest revenue_since_switch first.
        """
        termers = self.get_long_termers(target_date, min_nights, lookback_weeks)
        results = []
        for t in termers:
            res_id = t["reservation_id"]
            if not res_id:
                continue
            try:
                notes = self.get_reservation_notes(res_id)
            except Exception:
                continue
            tagged_notes = [n for n in notes if extend_tag in (n.get("reservationNote") or "").lower()]
            if not tagged_notes:
                continue
            note_date = None
            for n in tagged_notes:
                try:
                    d = datetime.strptime(n["dateCreated"][:10], "%Y-%m-%d").date()
                except (KeyError, ValueError):
                    continue
                if note_date is None or d < note_date:
                    note_date = d
            if note_date is None:
                continue

            try:
                payments = self.get_payments(res_id)
            except Exception:
                payments = []

            def _amt(p):
                try:
                    return float(p.get("amount") or 0)
                except (TypeError, ValueError):
                    return 0.0

            total_paid = sum(_amt(p) for p in payments)

            # Manual override wins outright -- check the most recently
            # created tagged note for a "$X" (staff typed this after checking
            # Cloudbeds' Credit Cards tab for a split the API can't see).
            override = None
            for n in sorted(tagged_notes, key=lambda n: n.get("dateCreated", ""), reverse=True):
                m = self._OVERRIDE_RE.search(n.get("reservationNote") or "")
                if m:
                    override = m
                    break

            if override:
                since_switch = float(override.group(1).replace(",", ""))
                override_date = self._parse_override_date(override.group(2)) if override.group(2) else None
                switch_date = override_date or note_date
                exact = override_date is not None
                manual_override = True
            else:
                # SYSTEM-run payments are the OTA's own automatic deposit
                # collection; everything else was entered by hand by staff,
                # which only happens once they've taken the guest over for
                # direct billing (see docstring -- this replaced an earlier
                # "skip the first payment" rule that wrongly excluded staff-
                # run first payments for guests who never had a SYSTEM
                # deposit at all).
                staff_payments = [p for p in payments if (p.get("userName") or "") != "SYSTEM"]
                if staff_payments:
                    switch_date = min(
                        datetime.strptime(p["transactionDateTime"][:10], "%Y-%m-%d").date()
                        for p in staff_payments)
                    exact = True
                    since_switch = sum(_amt(p) for p in staff_payments)
                else:
                    switch_date, exact, since_switch = note_date, False, 0.0
                manual_override = False

            results.append({
                "name": t["name"], "source": t["source"], "nights": t["nights"],
                "checkout": t["end"], "switch_date": switch_date, "switch_date_is_exact": exact,
                "manual_override": manual_override,
                "revenue_since_switch": round(since_switch, 2), "total_paid": round(total_paid, 2),
            })

        results.sort(key=lambda r: -r["revenue_since_switch"])
        return results

    def get_nightly_counts_for_range(self, date_from: date, date_to: date) -> list[int]:
        """Return occupied bed count for each night from date_from to date_to inclusive."""
        counts = []
        current = date_from
        while current <= date_to:
            counts.append(self.get_guests_in_house_count(current))
            current += timedelta(days=1)
        return counts

    def get_guests_in_house(self, target_date: date) -> list[dict]:
        """Full reservation objects for guests in house on target_date (matches Cloudbeds occupancy report)."""
        EXCLUDE = {"cancelled", "canceled", "no_show", "not_confirmed"}
        all_res = self._paginate_reservations_by_checkin(
            date(2020, 1, 1), target_date,
            extra_params={"checkOutFrom": target_date.isoformat()},
        )
        result = []
        for r in all_res:
            if r.get("status", "").lower() in EXCLUDE:
                continue
            try:
                ci = date.fromisoformat(r["startDate"])
                co = date.fromisoformat(r["endDate"])
                if ci <= target_date and co > target_date:
                    result.append(r)
            except (KeyError, ValueError):
                continue
        return result

    def get_guests_in_house_count(self, target_date: date) -> int:
        """
        Count beds occupied on target_date night using Cloudbeds' methodology:
        confirmed + checked_in + checked_out (guest was there for their scheduled nights).
        Uses checkOutFrom to scope correctly, then filters client-side.
        """
        all_res = self._paginate_reservations_by_checkin(
            date(2020, 1, 1), target_date,
            extra_params={"checkOutFrom": target_date.isoformat()},
        )
        EXCLUDE = {"cancelled", "canceled", "no_show", "not_confirmed"}
        count = 0
        for r in all_res:
            if r.get("status", "").lower() in EXCLUDE:
                continue
            try:
                ci = date.fromisoformat(r["startDate"])
                co = date.fromisoformat(r["endDate"])
                if ci <= target_date and co > target_date:
                    count += 1
            except (KeyError, ValueError):
                continue
        return count

    # -- Transactions ---------------------------------------------------------

    def get_transactions(self, date_from: date, date_to: date) -> list[dict]:
        return self._paginate_transactions({
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        })

    # -- Property -------------------------------------------------------------

    def get_bed_count(self) -> int:
        """Total beds in property from getRooms total field."""
        data = self._get("getRooms", {})
        total = int(data.get("total", 0))
        if total <= 0:
            raise RuntimeError(
                f"getRooms returned {total} beds — API may be returning bad data. "
                "Check Cloudbeds property configuration."
            )
        if total > 500:
            raise RuntimeError(
                f"getRooms returned {total} beds — suspiciously high. "
                "Verify this is not an API error before continuing."
            )
        return total

    # -- Data Insights (occupancy) --------------------------------------------

    def get_rooms_sold(self, date_from: date, date_to: date) -> list[dict]:
        """
        Fetch daily occupancy from Data Insights API. One POST per calendar year
        in the range. Returns list of dicts with keys: date (date), occupancy (float %),
        accommodations_booked (int), revenue (float).
        Matches the Cloudbeds occupancy report exactly.
        """
        results = []
        headers = {
            "X-PROPERTY-ID": self.property_id,
            "Content-Type": "application/json",
        }
        url = f"{DATAINSIGHTS_URL}/classic_reports/production_reports/rooms_sold"

        for year in range(date_from.year, date_to.year + 1):
            y_start = max(date_from, date(year, 1, 1))
            y_end   = min(date_to,   date(year, 12, 31))
            body = {
                "report_year":  str(year),
                "period_start": y_start.strftime("%m-%d"),
                "period_end":   y_end.strftime("%m-%d"),
                "grouping":     "day",
            }
            for attempt in range(3):
                try:
                    resp = self.session.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
                except requests.exceptions.RequestException as exc:
                    if attempt == 2:
                        raise RuntimeError(f"Data Insights network error for year {year} -- {exc}") from exc
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code in RETRYABLE_STATUS:
                    wait = int(resp.headers.get("Retry-After", 2 ** attempt * 5))
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            else:
                # All 3 attempts hit a retryable status — do not silently use the error body
                raise RuntimeError(
                    f"Data Insights rate/server error after 3 retries for year {year}. "
                    "Try again in a few minutes."
                )
            for item in resp.json().get("results", []):
                month, day = item["date"].split("-")
                results.append({
                    "date":                  date(year, int(month), int(day)),
                    "occupancy":             float(item.get("occupancy", 0)),
                    "accommodations_booked": int(item.get("accommodations_booked", 0)),
                    "revenue":               float(item.get("revenue", 0)),
                })
        return results
