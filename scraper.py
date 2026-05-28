#!/usr/bin/env python3
"""
Youth football tournament scraper.
Sources: youngtalentsgroup.com, ballfreunde.com, euro-sportring.com
Destination: Airtable
"""

import os
import re
import json
import time
import logging
import requests
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Airtable config ────────────────────────────────────────────────────────────
AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE = "appd7ZQJo2i52qD4r"
AIRTABLE_TABLE = "tbl3mUnkwPDsXgf0p"
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}"

FIELDS = {
    "name":          "fldHRlkRNVfNuOqts",
    "start_date":    "fldZMcZJBN0xYNSaJ",
    "end_date":      "fldLVMnvdLJKp2ZX3",
    "country":       "fldvSbGCkDfJ8e5OX",
    "city":          "fldoyK4A3BFcQ6KNH",
    "birth_year":    "fldvpSG2dFoNWN73m",
    "gender":        "fldkr5w87oN8w0ynq",
    "format":        "fldXuPrg7bpLdMEqR",
    "level":         "fldM8vxBKJgBiPZxX",
    "source_url":    "fldJxKv8NtlMO1nGc",
    "source_site":   "fldqFvR1T4s5wL2sY",
    "official_site": "fldR3T5sNpWjC4aLm",
    "min_teams":     "fldBnKpQ7xZ3sM8tV",
    "min_games":     "fldW2cHrT9yJ5nXpE",
    "game_duration": "fldP4mDsK1bN6vQjY",
    "contact":       "fldA7tRcL2sY8nWxB",
}

# ── youngtalentsgroup.com config ───────────────────────────────────────────────
YTG_BASE = "https://youngtalentsgroup.com"
YTG_SEARCH = f"{YTG_BASE}/search"

# Country IDs for allowed countries
YTG_ALLOWED_COUNTRIES = {
    "Bulgaria": 39,
    "Greece": 71,
    "Hungary": 50,
    "Portugal": 161,
    "Spain": 87,
    "Romania": 165,
    "Cyprus": 95,
}
YTG_ALLOWED_NAMES = {c.lower() for c in YTG_ALLOWED_COUNTRIES}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_date_ddmmyyyy(s: str) -> str:
    """Convert '12.06.2026' → '2026-06-12'."""
    try:
        return datetime.strptime(s.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return s.strip()


def parse_date_range(text: str):
    """Parse '12.06.2026 - 14.06.2026' → (start, end) in ISO format."""
    parts = text.strip().split(" - ")
    if len(parts) == 2:
        return parse_date_ddmmyyyy(parts[0]), parse_date_ddmmyyyy(parts[1])
    return None, None


def extract_price(text: str) -> str:
    """Extract numeric price from '€140' → '140'."""
    m = re.search(r"[\d,]+", text)
    return m.group(0).replace(",", "") if m else ""


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ── youngtalentsgroup.com scraper ──────────────────────────────────────────────

def ytg_parse_cards(html: str) -> list[dict]:
    """Parse tournament cards from HTML (works for both page 1 and AJAX pages)."""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.tournament-card.card-horizontal")
    results = []

    for card in cards:
        try:
            t = _ytg_parse_card(card)
            if t:
                results.append(t)
        except Exception as e:
            log.warning("Failed to parse card: %s", e)

    return results


def _ytg_parse_card(card) -> dict | None:
    link_el = card.select_one("a.card__link")
    if not link_el:
        return None
    url_path = link_el.get("href", "")
    url = YTG_BASE + url_path if url_path.startswith("/") else url_path

    # Tournament name from URL path segment
    name = url_path.rstrip("/").split("/")[-1].replace("-", " ").title()

    # Level (Pro / Amateur / etc.)
    level_el = card.select_one("span.tour-type")
    level = level_el.get_text(strip=True) if level_el else ""

    # Planned teams
    teams_el = card.select_one("div.text-secondary")
    planned_teams = teams_el.get_text(strip=True) if teams_el else ""

    # Location: city and country from anchor tags inside card
    location_anchors = card.select("a[href^='/tournaments/']")
    city, country = "", ""
    for a in location_anchors:
        href = a.get("href", "")
        parts = [p for p in href.split("/") if p]
        # /tournaments/spain/rojales → parts = ['tournaments','spain','rojales']
        if len(parts) == 3:
            city = a.get_text(strip=True).rstrip(",").strip()
        elif len(parts) == 2:
            country = a.get_text(strip=True).strip()

    # Filter by allowed country
    if country.lower() not in YTG_ALLOWED_NAMES:
        return None

    # Dates
    date_p = card.find("p", string=re.compile(r"\d{2}\.\d{2}\.\d{4}"))
    start_date, end_date = None, None
    if date_p:
        start_date, end_date = parse_date_range(date_p.get_text(strip=True))

    # Birth year and format  e.g. "2018 B8 (7vs7)"
    birth_year, gender, fmt = "", "", ""
    tooltip_el = card.select_one("span.tooltip")
    if tooltip_el:
        birth_year = tooltip_el.get_text(strip=True)
        # Text after tooltip sibling contains gender+format
        sibling_text = tooltip_el.next_sibling
        if sibling_text:
            raw = str(sibling_text).strip()
            # e.g. "B8 (7vs7)" or "G8 (7vs7)"
            m = re.search(r"([BG])\d+\s+\(([^)]+)\)", raw)
            if m:
                gender = "Boys" if m.group(1) == "B" else "Girls"
                fmt = m.group(2)

    # Rating
    rating_el = card.select_one("span.card-label")
    rating = rating_el.get_text(strip=True) if rating_el else ""

    # Fees — two price__item elements: first = registration, second = accommodation
    price_els = card.select("p.price__item")
    reg_fee = extract_price(price_els[0].get_text()) if len(price_els) > 0 else ""
    acc_fee = extract_price(price_els[1].get_text()) if len(price_els) > 1 else ""

    return {
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "country": country,
        "city": city,
        "birth_year": birth_year,
        "gender": gender,
        "format": fmt,
        "level": level,
        "planned_teams": planned_teams,
        "rating": rating,
        "registration_fee": reg_fee,
        "accommodation_fee": acc_fee,
        "source_url": url,
        "source_site": "youngtalentsgroup.com",
        "official_site": url,
    }


def ytg_scrape_page1(session: requests.Session) -> tuple[list[dict], bool]:
    """Fetch and parse search page 1 (full HTML). Returns (tournaments, has_more)."""
    log.info("YTG: fetching page 1")
    resp = session.get(YTG_SEARCH, params={"page": 1}, timeout=30)
    resp.raise_for_status()
    tournaments = ytg_parse_cards(resp.text)

    # Check if pagination exists
    soup = BeautifulSoup(resp.text, "lxml")
    has_more = bool(soup.select_one("a[href*='page=2'], nav.pagination"))
    return tournaments, has_more


def ytg_scrape_ajax(session: requests.Session, page: int) -> tuple[list[dict], bool]:
    """Fetch AJAX page N (returns JSON with 'html' key). Returns (tournaments, has_more)."""
    log.info("YTG: fetching page %d (AJAX)", page)
    resp = session.get(YTG_SEARCH, params={"page": page}, timeout=30,
                       headers={"X-Requested-With": "XMLHttpRequest",
                                "Accept": "application/json, text/javascript, */*"})
    resp.raise_for_status()

    try:
        data = resp.json()
        html = data.get("html", "")
        has_more = bool(data.get("has_pagination", False))
    except json.JSONDecodeError:
        # Fell back to full HTML — parse it directly
        html = resp.text
        soup = BeautifulSoup(html, "lxml")
        has_more = bool(soup.select_one("nav.pagination"))

    tournaments = ytg_parse_cards(html)
    return tournaments, has_more


def ytg_scrape_all() -> list[dict]:
    """Scrape all pages from youngtalentsgroup.com."""
    session = get_session()
    all_tournaments = []

    try:
        page1, has_more = ytg_scrape_page1(session)
        all_tournaments.extend(page1)
        log.info("YTG: page 1 → %d tournaments (filtered)", len(page1))
    except Exception as e:
        log.error("YTG: page 1 failed: %s", e)
        return all_tournaments

    page = 2
    while has_more:
        try:
            time.sleep(1)  # polite delay
            page_results, has_more = ytg_scrape_ajax(session, page)
            all_tournaments.extend(page_results)
            log.info("YTG: page %d → %d tournaments (filtered)", page, len(page_results))
            page += 1
            if page > 50:  # safety cap
                break
        except Exception as e:
            log.error("YTG: page %d failed: %s", page, e)
            break

    log.info("YTG: total %d tournaments scraped", len(all_tournaments))
    return all_tournaments


# ── ballfreunde.com scraper ────────────────────────────────────────────────────
# TODO: investigate site structure before implementing

def ballfreunde_scrape_all() -> list[dict]:
    log.info("ballfreunde.com: scraper not yet implemented")
    return []


# ── euro-sportring.com scraper ─────────────────────────────────────────────────
# TODO: investigate site structure before implementing

def eurosportring_scrape_all() -> list[dict]:
    log.info("euro-sportring.com: scraper not yet implemented")
    return []


# ── Airtable integration ───────────────────────────────────────────────────────

def airtable_headers() -> dict:
    return {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json",
    }


def airtable_fetch_existing() -> set[str]:
    """Return set of dedup keys (name|birth_year|start_date) already in Airtable."""
    keys = set()
    offset = None
    while True:
        params = {
            "fields[]": [
                FIELDS["name"],
                FIELDS["birth_year"],
                FIELDS["start_date"],
            ],
            "pageSize": 100,
        }
        if offset:
            params["offset"] = offset

        resp = requests.get(AIRTABLE_URL, headers=airtable_headers(), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for record in data.get("records", []):
            f = record.get("fields", {})
            key = _dedup_key(
                f.get(FIELDS["name"], ""),
                str(f.get(FIELDS["birth_year"], "")),
                f.get(FIELDS["start_date"], ""),
            )
            keys.add(key)

        offset = data.get("offset")
        if not offset:
            break

    log.info("Airtable: %d existing records loaded", len(keys))
    return keys


def _dedup_key(name: str, birth_year: str, start_date: str) -> str:
    return f"{name.lower().strip()}|{birth_year.strip()}|{start_date.strip()}"


def tournament_to_airtable_fields(t: dict) -> dict:
    fields = {}

    def set_field(key, value):
        if value is not None and value != "":
            fields[FIELDS[key]] = value

    set_field("name", t.get("name"))
    set_field("start_date", t.get("start_date"))
    set_field("end_date", t.get("end_date"))
    set_field("country", t.get("country"))
    set_field("city", t.get("city"))
    set_field("birth_year", t.get("birth_year"))
    set_field("gender", t.get("gender"))
    set_field("format", t.get("format"))
    set_field("level", t.get("level"))
    set_field("source_url", t.get("source_url"))
    set_field("source_site", t.get("source_site"))
    set_field("official_site", t.get("official_site"))
    set_field("min_teams", t.get("min_teams"))
    set_field("min_games", t.get("min_games"))
    set_field("game_duration", t.get("game_duration"))
    set_field("contact", t.get("contact"))

    return fields


def airtable_insert_batch(records: list[dict]) -> int:
    """Insert up to 10 records at a time. Returns count inserted."""
    inserted = 0
    for i in range(0, len(records), 10):
        batch = records[i:i + 10]
        payload = {"records": [{"fields": r} for r in batch]}
        resp = requests.post(AIRTABLE_URL, headers=airtable_headers(),
                             json=payload, timeout=30)
        if resp.status_code == 422:
            log.error("Airtable 422: %s", resp.text)
        else:
            resp.raise_for_status()
            inserted += len(resp.json().get("records", []))
        time.sleep(0.25)  # stay under 5 req/s limit
    return inserted


def upsert_tournaments(tournaments: list[dict]) -> None:
    if not tournaments:
        log.info("No tournaments to insert")
        return

    existing_keys = airtable_fetch_existing()
    new_records = []

    for t in tournaments:
        key = _dedup_key(
            t.get("name", ""),
            str(t.get("birth_year", "")),
            t.get("start_date", "") or "",
        )
        if key in existing_keys:
            continue
        fields = tournament_to_airtable_fields(t)
        if fields.get(FIELDS["name"]):
            new_records.append(fields)
            existing_keys.add(key)  # avoid dupe within this run

    log.info("Inserting %d new records into Airtable", len(new_records))
    if new_records:
        inserted = airtable_insert_batch(new_records)
        log.info("Airtable: %d records inserted", inserted)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    all_tournaments = []

    all_tournaments.extend(ytg_scrape_all())
    all_tournaments.extend(ballfreunde_scrape_all())
    all_tournaments.extend(eurosportring_scrape_all())

    log.info("Total tournaments collected: %d", len(all_tournaments))
    upsert_tournaments(all_tournaments)
    log.info("Done.")


if __name__ == "__main__":
    main()
