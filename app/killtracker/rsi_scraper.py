"""Direct RSI profile scraper (replaces handlers/fetchRSIProfileData.js).

Run only inside Celery — uses blocking requests. Keep robust to layout changes.
"""
import logging
import re
from typing import TypedDict

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

RSI_CITIZEN_URL = "https://robertsspaceindustries.com/citizens/{}"
DEFAULT_AVATAR = "https://cdn.robertsspaceindustries.com/static/images/account/avatar_default_big.jpg"

# Only fetch things that look like actual RSI handles — anything else would let a caller
# steer the request path (e.g. "../orgs/x" or "foo?bar") on the RSI site.
HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RSIProfile(TypedDict, total=False):
    handle: str
    display_name: str
    org_name: str
    org_short: str
    avatar_url: str
    enlisted_date: str
    bio: str
    website: str
    location: str
    exists: bool


def fetch_rsi_profile(handle: str) -> RSIProfile:
    out: RSIProfile = {"handle": handle, "display_name": "", "org_name": "No organization",
                      "org_short": "", "avatar_url": "", "enlisted_date": "",
                      "bio": "", "website": "", "location": "", "exists": False}
    if not HANDLE_RE.match(handle or ""):
        return out
    try:
        r = requests.get(RSI_CITIZEN_URL.format(handle), timeout=10,
                         headers={"User-Agent": "Mozilla/5.0 BlightVeilKillTracker"})
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "html.parser")
        out["exists"] = True

        # Display name (moniker) — .profile .info .entry:first .value
        name_el = soup.select_one(".profile .info .entry .value")
        if name_el:
            out["display_name"] = name_el.get_text(strip=True)

        # Avatar
        img = soup.select_one(".profile .thumb img")
        if img and img.get("src"):
            src = img["src"]
            out["avatar_url"] = src if src.startswith("http") else f"https://robertsspaceindustries.com{src}"

        # Org
        org_a = soup.select_one(".main-org .info .entry .value")
        if org_a:
            out["org_name"] = org_a.get_text(strip=True)
        sid = soup.select_one(".main-org .info a")
        if sid and sid.get("href"):
            m = re.search(r"/orgs/([A-Z0-9]+)", sid["href"])
            if m:
                out["org_short"] = m.group(1)

        # Entries are scattered across the page — scan every .entry with label+value
        for entry in soup.select(".entry"):
            lbl_el = entry.select_one(".label")
            val_el = entry.select_one(".value")
            if not lbl_el or not val_el:
                continue
            key = lbl_el.get_text(strip=True).lower()
            if "enlisted" in key and not out["enlisted_date"]:
                out["enlisted_date"] = val_el.get_text(" ", strip=True)
            elif "location" in key and not out["location"]:
                out["location"] = val_el.get_text(" ", strip=True)
            elif "website" in key and not out["website"]:
                a = val_el.find("a")
                out["website"] = a["href"] if a and a.get("href") else val_el.get_text(strip=True)
            elif "bio" in key and not out["bio"]:
                out["bio"] = val_el.get_text(" ", strip=True)
    except Exception as e:
        logger.warning("[RSI] scrape failed for %s: %s", handle, e)
    return out
