"""
fetch_scholar.py (ScraperAPI version, with retries and proxy escalation)

Fetches the Google Scholar author profile through ScraperAPI, parses the
publication list, and writes publications.html and publications.json.

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
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")
SCHOLAR_ID = os.environ.get("SCHOLAR_ID")

if not SCRAPERAPI_KEY or not SCHOLAR_ID:
    print("ERROR: SCRAPERAPI_KEY and SCHOLAR_ID environment variables must be set.")
    sys.exit(1)

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
SCHOLAR_BASE_URL = "https://scholar.google.com/citations"
PAGE_SIZE = 100

# Each attempt uses a stronger (and more credit-hungry) proxy mode than the last.
# Google Scholar usually needs "premium" or higher.
ATTEMPTS = [
    {"country_code": "us"},
    {"premium": "true", "country_code": "us"},
    {"premium": "true", "country_code": "us"},
    {"ultra_premium": "true"},
]


def fetch_page_html(cstart):
    """Fetches one page of the author's profile via ScraperAPI, escalating on failure."""
    target_url = (
        f"{SCHOLAR_BASE_URL}?hl=en&user={SCHOLAR_ID}"
        f"&cstart={cstart}&pagesize={PAGE_SIZE}"
    )
    last_error = None

    for i, extra in enumerate(ATTEMPTS, start=1):
        params = {"api_key": SCRAPERAPI_KEY, "url": target_url, **extra}
        mode = ",".join(extra.keys())
        print(f"  attempt {i}/{len(ATTEMPTS)} (mode: {mode})...", flush=True)
        try:
            resp = requests.get(SCRAPERAPI_ENDPOINT, params=params, timeout=90)
        except requests.exceptions.RequestException as e:
            last_error = e
            print(f"  network error: {e}", flush=True)
            time.sleep(10)
            continue

        if resp.status_code == 200 and "gsc_a_tr" in resp.text or (
            resp.status_code == 200 and "gsc_a_nn" in resp.text
        ):
            return resp.text

        # Print the body so the real reason shows up in the Actions log.
        snippet = resp.text[:300].replace("\n", " ")
        print(f"  status {resp.status_code}: {snippet}", flush=True)
        last_error = f"HTTP {resp.status_code}"

        if resp.status_code in (401, 403):
            # Bad key or out of credits - escalating won't help.
            break
        time.sleep(10 * i)

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
        time.sleep(1)

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
