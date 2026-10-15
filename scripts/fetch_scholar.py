"""
fetch_scholar.py (ScraperAPI version, render + escalation)

Fetches the Google Scholar author profile through ScraperAPI, parses the
publication list, and writes publications.html and publications.json.

Changes vs the old version (per ScraperAPI support advice):
  - render=true is always used (plain requests now return 500)
  - proxy tiers are tried one at a time, never premium + ultra_premium together
  - all ScraperAPI params come BEFORE the target url, which is URL-encoded
  - timeout is 75s (ScraperAPI retries internally for up to 70s)
  - a 200 response that is actually Google's sign-in page counts as a failure

Environment variables required:
  SCRAPERAPI_KEY - your ScraperAPI API key
  SCHOLAR_ID     - the Google Scholar author id (the 'user=' value in the profile URL)
"""

import os
import sys
import json
import time
import html as htmllib
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")
SCHOLAR_ID = os.environ.get("SCHOLAR_ID")

if not SCRAPERAPI_KEY or not SCHOLAR_ID:
    print("ERROR: SCRAPERAPI_KEY and SCHOLAR_ID environment variables must be set.")
    sys.exit(1)

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
SCHOLAR_BASE_URL = "https://scholar.google.com/citations"
PAGE_SIZE = 100
TIMEOUT = 75

# Tried in order. premium and ultra_premium are never combined.
ATTEMPTS = [
    {"render": "true", "country_code": "us"},
    {"render": "true", "premium": "true", "country_code": "us"},
    {"render": "true", "ultra_premium": "true"},
]


def looks_like_profile(text):
    """True if the HTML is a real Scholar profile page (not a sign-in/consent page)."""
    return "gsc_a_tr" in text or "gsc_prf_in" in text


def looks_like_signin(text):
    return "accounts.google.com" in text and not looks_like_profile(text)


def fetch_page_html(cstart):
    target_url = (
        f"{SCHOLAR_BASE_URL}?hl=en&user={SCHOLAR_ID}"
        f"&cstart={cstart}&pagesize={PAGE_SIZE}"
    )
    last_error = None

    for i, extra in enumerate(ATTEMPTS, start=1):
        # Order matters: ScraperAPI params first, url LAST (urlencode keeps dict order
        # and percent-encodes the target URL).
        params = {"api_key": SCRAPERAPI_KEY, **extra, "url": target_url}
        mode = "+".join(extra.keys())
        print(f"  attempt {i}/{len(ATTEMPTS)} (mode: {mode})...", flush=True)
        try:
            resp = requests.get(
                f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}", timeout=TIMEOUT
            )
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            print(f"  network error: {e}", flush=True)
            time.sleep(5)
            continue

        if resp.status_code == 200:
            if looks_like_profile(resp.text):
                return resp.text
            if looks_like_signin(resp.text):
                print("  got 200 but it is Google's sign-in page; escalating.", flush=True)
                last_error = "Google sign-in page returned"
            else:
                print("  got 200 but no profile markup found; escalating.", flush=True)
                last_error = "unrecognised page"
            time.sleep(3)
            continue

        snippet = resp.text[:300].replace("\n", " ")
        print(f"  status {resp.status_code}: {snippet}", flush=True)
        last_error = f"HTTP {resp.status_code}"
        if resp.status_code in (401, 403):
            break  # bad key / out of credits - escalating won't help
        time.sleep(5)

    raise RuntimeError(f"Could not fetch Scholar page (cstart={cstart}): {last_error}")


def parse_publications(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr.gsc_a_tr")

    publications = []
    for row in rows:
        title_tag = row.select_one("a.gsc_a_at")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = title_tag.get("href", "")
        link = f"https://scholar.google.com{href}" if href.startswith("/") else href

        gray_divs = row.select("div.gs_gray")
        authors = gray_divs[0].get_text(strip=True) if len(gray_divs) > 0 else ""
        venue = gray_divs[1].get_text(strip=True) if len(gray_divs) > 1 else ""

        year_tag = row.select_one("span.gsc_a_h")
        year = year_tag.get_text(strip=True) if year_tag else ""

        cited_tag = row.select_one("a.gsc_a_ac")
        cited_by = cited_tag.get_text(strip=True) if cited_tag else ""

        publications.append({
            "title": title,
            "link": link,
            "authors": authors,
            "venue": venue,
            "year": year,
            "cited_by": cited_by,
        })

    return publications


def fetch_all_publications():
    all_pubs = []
    cstart = 0

    while True:
        print(f"Fetching profile page starting at cstart={cstart}...", flush=True)
        html = fetch_page_html(cstart)
        pubs = parse_publications(html)

        if not pubs:
            break

        all_pubs.extend(pubs)
        print(f"  -> got {len(pubs)} publications (total so far: {len(all_pubs)})", flush=True)

        if len(pubs) < PAGE_SIZE:
            break

        cstart += PAGE_SIZE
        time.sleep(2)

    return all_pubs


def build_html(publications):
    items = []

    def sort_key(p):
        try:
            return -int(p.get("year", 0))
        except (ValueError, TypeError):
            return 0

    esc = htmllib.escape
    for pub in sorted(publications, key=sort_key):
        title = esc(pub.get("title", "Untitled"))
        link = esc(pub.get("link", "#"), quote=True)
        authors = esc(pub.get("authors", ""))
        venue = esc(pub.get("venue", ""))

        items.append(f"""
        <li class="pub-item">
            <a href="{link}" target="_blank" rel="noopener noreferrer" class="pub-title">{title}</a>
            <div class="pub-authors">{authors}</div>
            <div class="pub-venue">{venue}</div>
        </li>""")

    return f"""<!-- Auto-generated by fetch_scholar.py — do not edit manually -->
<ul class="publications-list">
{''.join(items)}
</ul>
"""


def main():
    publications = fetch_all_publications()
    print(f"Fetched {len(publications)} publications total.", flush=True)

    # Safety: never overwrite the existing list with an empty result.
    if not publications:
        print("ERROR: no publications parsed; keeping existing files.")
        sys.exit(1)

    with open("publications.json", "w", encoding="utf-8") as f:
        json.dump(publications, f, ensure_ascii=False, indent=2)

    with open("publications.html", "w", encoding="utf-8") as f:
        f.write(build_html(publications))

    print("Wrote publications.json and publications.html", flush=True)


if __name__ == "__main__":
    main()
