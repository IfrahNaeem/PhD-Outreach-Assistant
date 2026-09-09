"""
page_fetcher.py — Fetches a single, specific URL the user explicitly provides,
and converts it to clean visible text.

This deliberately does NOT crawl, follow links, or discover pages on its own.
It fetches exactly the one URL it's given, once. The user chooses which page
that is — this module has no logic for finding pages by itself.
"""

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; PhDOutreachAssistant/1.0; +personal research tool)"
MAX_CHARS = 15000  # keep the extraction prompt a reasonable size


def fetch_page_text(url):
    """Returns (ok, message, text). Fetches exactly the given URL, once."""
    url = (url or "").strip()
    if not url:
        return False, "Enter a URL first.", None
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except requests.exceptions.Timeout:
        return False, "The page took too long to respond (timed out).", None
    except requests.exceptions.ConnectionError as e:
        return False, f"Could not reach that URL: {e}", None
    except Exception as e:
        return False, f"Could not fetch that URL: {e}", None

    if resp.status_code != 200:
        return False, f"That page returned an error (HTTP {resp.status_code}).", None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [ln.strip() for ln in text.splitlines()]
        text = "\n".join(ln for ln in lines if ln)
    except Exception as e:
        return False, f"Fetched the page but could not parse it: {e}", None

    if not text.strip():
        return False, "That page loaded but had no readable text (it may require JavaScript, which this tool can't run).", None

    truncated = text[:MAX_CHARS]
    return True, f"Fetched {len(text)} characters of text.", truncated
