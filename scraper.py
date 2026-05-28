"""
Youth football tournament scraper.
Sources: youngtalentsgroup.com, ballfreunde.com, euro-sportring.com
Target: Airtable base appd7ZQJo2i52qD4r, table tbl3mUnkwPDsXgf0p
"""

import os
import re
import time
import requests
from datetime import datetime, date
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE_ID = "appd7ZQJo2i52qD4r"
AIRTABLE_TABLE_ID = "tbl3mUnkwPDsXgf0p"

AIRTABLE_FIELDS = {
    "name":           "fldHRlkRNVfNuOqts",
    "start_date":     "fldZMcZJBN0xYNSaJ",
    "end_date":       "fldLVMnvdLJKp2ZX3",
    "country":        "fldvSbGCkDfJ8e5OX",
    "city":           "fldoyK4A3BFcQ6KNH",
    "birth_year":     "fldvpSG2dFoNWN73m",
    "gender":         "fldkr5w87oN8w0ynq",
    "format":         "fldXuPrg7bpLdMEqR",
    "level":          "fldM8vxBKJgBiPZxX",
    "source_url":     "fldJxKv8NtlMO1nGc",
    "source_site":    "fldqFvR1T4s5wL2sY",
    "official_site":  "fldR3T5sNpWjC4aLm",
    "min_teams":      "fldBnKpQ7xZ3sM8tV",
    "min_games":      "fldW2cHrT9yJ5nXpE",
    "game_duration":  "fldP4mDsK1bN6vQjY",
    "contact":        "fldA7tRcL2sY8nWxB",
}

ALLOWED_COUNTRIES = {
    "cyprus", "greece", "bulgaria", "portugal", "spain", "hungary", "romania"
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_get(url: str, retries: int = 3, **kwargs) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=20, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"  [warn] GET {url} failed ({e}), attempt {attempt+1}/{retries}")
            time.sleep(2 ** attempt)
    return None


def parse_date(s: str) -> str | None:
    """Parse DD.MM.YYYY → YYYY-MM-DD for Airtable."""
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def country_allowed(country: str) -> bool:
    return country.strip().lower() in ALLOWED_COUNTRIES


# ---------------------------------------------------------------------------
# Airtable
# ---------------------------------------------------------------------------

def airtable_existing_keys() -> set[str]:
    """Return set of 'name|birth_year|start_date' for deduplication."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    keys = set()
    offset = None
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for rec in data.get("records", []):
            f = rec.get("fields", {})
            name = f.get(AIRTABLE_FIELDS["name"], "")
            by = str(f.get(AIRTABLE_FIELDS["birth_year"], ""))
            sd = f.get(AIRTABLE_FIELDS["start_date"], "")
            keys.add(f"{name}|{by}|{sd}")
        offset = data.get("offset")
        if not offset:
            break
    print(f"[airtable] {len(keys)} existing records loaded")
    return keys


def airtable_insert(records: list[dict]) -> int:
    """Insert records in batches of 10. Returns count inserted."""
    if not records:
        return 0
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json",
    }
    inserted = 0
    for i in range(0, len(records), 10):
        batch = records[i:i+10]
        payload = {"records": [{"fields": r} for r in batch]}
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        r.raise_for_status()
        inserted += len(r.json().get("records", []))
        time.sleep(0.25)  # Airtable rate limit: 5 req/s
    return inserted


# ---------------------------------------------------------------------------
# Site 1: youngtalentsgroup.com
# ---------------------------------------------------------------------------

def scrape_youngtalents() -> list[dict]:
    base = "https://youngtalentsgroup.com"
    tournaments = []
    page = 1

    while True:
        print(f"  [youngtalents] page {page}")
        url = f"{base}/search?page={page}"
        r = safe_get(url, headers={"Accept": "application/json"})
        if not r:
            break

        try:
            data = r.json()
        except Exception:
            print("  [youngtalents] non-JSON response, stopping")
            break

        html = data.get("html", "")
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".tournament-card")
        if not cards:
            break

        for card in cards:
            t = _parse_youngtalents_card(card, base)
            if t:
                tournaments.append(t)

        if not data.get("has_pagination"):
            break
        page += 1
        time.sleep(0.5)

    print(f"  [youngtalents] {len(tournaments)} tournaments scraped")
    return tournaments


def _parse_youngtalents_card(card, base: str) -> dict | None:
    try:
        link_tag = card.select_one("a.card__link")
        name_tag = card.select_one("h4")
        if not name_tag:
            return None

        name = name_tag.get_text(strip=True)
        relative_url = link_tag["href"] if link_tag else ""
        source_url = base + relative_url if relative_url else ""

        # Dates: "30.06.2026 - 05.07.2026"
        date_tag = card.select_one("p")
        start_date = end_date = None
        if date_tag:
            parts = date_tag.get_text(strip=True).split(" - ")
            if len(parts) == 2:
                start_date = parse_date(parts[0])
                end_date = parse_date(parts[1])

        # Country / city via anchor tags inside card
        anchors = card.select("a[href^='/tournaments/']")
        country = city = ""
        for a in anchors:
            href = a["href"]
            depth = href.strip("/").split("/")
            # /tournaments/spain → country
            # /tournaments/spain/terrassa → city
            if len(depth) == 2:
                country = a.get_text(strip=True)
            elif len(depth) == 3:
                city = a.get_text(strip=True)

        if not country_allowed(country):
            return None

        # Birth year from tooltip
        birth_year = None
        tooltip = card.select_one(".tooltip")
        if tooltip:
            try:
                birth_year = int(tooltip.get_text(strip=True))
            except ValueError:
                pass

        # Gender from card text (B14, G12, U10 …)
        card_text = card.get_text(" ", strip=True)
        gender = None
        m = re.search(r"\b([BGU])(\d{1,2})\b", card_text)
        if m:
            prefix = m.group(1)
            gender = "Boys" if prefix == "B" else "Girls" if prefix == "G" else "Mixed"

        # Format (11vs11, 7vs7 …)
        fmt = None
        m2 = re.search(r"(\d+vs\d+|\d+x\d+|\d+\s*v\s*\d+)", card_text, re.IGNORECASE)
        if m2:
            fmt = m2.group(1)

        # Level
        level = None
        level_tag = card.select_one(".tour-type")
        if level_tag:
            level = level_tag.get_text(strip=True)

        return {
            AIRTABLE_FIELDS["name"]:        name,
            AIRTABLE_FIELDS["start_date"]:  start_date,
            AIRTABLE_FIELDS["end_date"]:    end_date,
            AIRTABLE_FIELDS["country"]:     country,
            AIRTABLE_FIELDS["city"]:        city,
            AIRTABLE_FIELDS["birth_year"]:  birth_year,
            AIRTABLE_FIELDS["gender"]:      gender,
            AIRTABLE_FIELDS["format"]:      fmt,
            AIRTABLE_FIELDS["level"]:       level,
            AIRTABLE_FIELDS["source_url"]:  source_url,
            AIRTABLE_FIELDS["source_site"]: "youngtalentsgroup.com",
        }
    except Exception as e:
        print(f"  [youngtalents] card parse error: {e}")
        return None


# ---------------------------------------------------------------------------
# Site 2: ballfreunde.com
# ---------------------------------------------------------------------------

def scrape_ballfreunde() -> list[dict]:
    """
    Ballfreunde lists tournaments at /turniere (German).
    Tries to find a tournament list page and parse it.
    """
    base = "https://www.ballfreunde.com"
    tournaments = []

    # Common listing paths to try
    candidate_paths = ["/turniere", "/tournaments", "/en/tournaments", "/en/turniere"]
    list_url = None
    for path in candidate_paths:
        r = safe_get(base + path)
        if r and r.status_code == 200:
            list_url = base + path
            break

    if not list_url:
        print("  [ballfreunde] could not find tournament listing page")
        return []

    r = safe_get(list_url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    # Grab any link that looks like a tournament detail page
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not any(k in href for k in ["/turnier", "/tournament", "/cup", "/liga"]):
            continue
        full_url = href if href.startswith("http") else base + href
        t = _scrape_ballfreunde_detail(full_url)
        if t:
            tournaments.append(t)
        time.sleep(0.4)

    print(f"  [ballfreunde] {len(tournaments)} tournaments scraped")
    return tournaments


def _scrape_ballfreunde_detail(url: str) -> dict | None:
    r = safe_get(url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")

    title = soup.find("h1") or soup.find("h2")
    name = title.get_text(strip=True) if title else ""
    if not name:
        return None

    text = soup.get_text(" ", strip=True)

    # Dates
    start_date = end_date = None
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})", text)
    if m:
        start_date = parse_date(m.group(1))
        end_date = parse_date(m.group(2))

    # Country
    country = ""
    for c in ALLOWED_COUNTRIES:
        if c.lower() in text.lower():
            country = c.capitalize()
            break
    if not country_allowed(country):
        return None

    return {
        AIRTABLE_FIELDS["name"]:        name,
        AIRTABLE_FIELDS["start_date"]:  start_date,
        AIRTABLE_FIELDS["end_date"]:    end_date,
        AIRTABLE_FIELDS["country"]:     country,
        AIRTABLE_FIELDS["source_url"]:  url,
        AIRTABLE_FIELDS["source_site"]: "ballfreunde.com",
    }


# ---------------------------------------------------------------------------
# Site 3: euro-sportring.com
# ---------------------------------------------------------------------------

def scrape_eurosportring() -> list[dict]:
    base = "https://www.euro-sportring.com"
    tournaments = []

    candidate_paths = [
        "/football/tournaments",
        "/football",
        "/sports/football",
        "/en/football/tournaments",
    ]
    list_url = None
    for path in candidate_paths:
        r = safe_get(base + path)
        if r and r.status_code == 200:
            list_url = base + path
            break

    if not list_url:
        print("  [euro-sportring] could not find tournament listing page")
        return []

    r = safe_get(list_url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    detail_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(k in href.lower() for k in ["tournament", "cup", "football"]):
            full = href if href.startswith("http") else base + href
            if full != list_url:
                detail_links.add(full)

    for url in detail_links:
        t = _scrape_eurosportring_detail(url)
        if t:
            tournaments.append(t)
        time.sleep(0.4)

    print(f"  [euro-sportring] {len(tournaments)} tournaments scraped")
    return tournaments


def _scrape_eurosportring_detail(url: str) -> dict | None:
    r = safe_get(url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")

    title = soup.find("h1") or soup.find("h2")
    name = title.get_text(strip=True) if title else ""
    if not name:
        return None

    text = soup.get_text(" ", strip=True)

    start_date = end_date = None
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})", text)
    if m:
        start_date = parse_date(m.group(1))
        end_date = parse_date(m.group(2))

    country = ""
    for c in ALLOWED_COUNTRIES:
        if c.lower() in text.lower():
            country = c.capitalize()
            break
    if not country_allowed(country):
        return None

    # Birth year
    birth_year = None
    m2 = re.search(r"\b(20\d{2})\b", text)
    if m2:
        birth_year = int(m2.group(1))

    return {
        AIRTABLE_FIELDS["name"]:        name,
        AIRTABLE_FIELDS["start_date"]:  start_date,
        AIRTABLE_FIELDS["end_date"]:    end_date,
        AIRTABLE_FIELDS["country"]:     country,
        AIRTABLE_FIELDS["birth_year"]:  birth_year,
        AIRTABLE_FIELDS["source_url"]:  url,
        AIRTABLE_FIELDS["source_site"]: "euro-sportring.com",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def dedup_key(r: dict) -> str:
    name = r.get(AIRTABLE_FIELDS["name"], "")
    by = str(r.get(AIRTABLE_FIELDS["birth_year"], ""))
    sd = r.get(AIRTABLE_FIELDS["start_date"], "")
    return f"{name}|{by}|{sd}"


def main():
    print(f"=== Scraper started at {datetime.utcnow().isoformat()} ===")

    existing = airtable_existing_keys()

    all_tournaments: list[dict] = []

    print("[1/3] Scraping youngtalentsgroup.com ...")
    all_tournaments += scrape_youngtalents()

    print("[2/3] Scraping ballfreunde.com ...")
    all_tournaments += scrape_ballfreunde()

    print("[3/3] Scraping euro-sportring.com ...")
    all_tournaments += scrape_eurosportring()

    print(f"\nTotal scraped: {len(all_tournaments)}")

    # Filter out already-existing records
    new_records = [t for t in all_tournaments if dedup_key(t) not in existing]
    print(f"New (after dedup): {len(new_records)}")

    # Remove None-value fields before inserting
    cleaned = []
    for rec in new_records:
        cleaned.append({k: v for k, v in rec.items() if v is not None})

    inserted = airtable_insert(cleaned)
    print(f"Inserted into Airtable: {inserted}")
    print("=== Done ===")


if __name__ == "__main__":
    main()
