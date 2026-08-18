"""Find legally free copies of course readings and textbooks.

Two distinct problems with two distinct source sets:

  Journal articles  -> DOI-based open-access lookup (OpenAlex, Unpaywall, CORE).
                       Authoritative and structured: these services exist to
                       report whether a legal OA copy is registered.

  Textbooks         -> open educational resources, public-domain texts, and
                       lending libraries. Commercial textbooks almost never
                       appear in OA paper indexes, so searching them there
                       returns nothing.

ALLOWED_DOMAINS is an allowlist, deliberately. A blocklist drifts as new mirrors
appear; an allowlist can only ever return sources that were vetted going in.
"""
import re
import sqlite3
from datetime import datetime, timezone

import httpx

from .. import config

# Open educational resources, public domain, and lending libraries.
BOOK_DOMAINS = [
    "openstax.org",          # free peer-reviewed intro textbooks
    "libretexts.org",        # large OER library
    "open.umn.edu",          # Open Textbook Library
    "oercommons.org",
    "pressbooks.pub",
    "doabooks.org",          # Directory of Open Access Books
    "gutenberg.org",         # public domain
    "standardebooks.org",
    "openlibrary.org",       # controlled digital lending
    "archive.org",
    "hathitrust.org",
    "ocw.mit.edu",           # course materials
]

# Open-access scholarly infrastructure.
PAPER_DOMAINS = [
    "openalex.org", "core.ac.uk", "arxiv.org", "doaj.org",
    "ncbi.nlm.nih.gov", "semanticscholar.org", "zenodo.org",
    "biorxiv.org", "medrxiv.org", "ssrn.com",
]

ALLOWED_DOMAINS = BOOK_DOMAINS + PAPER_DOMAINS

UA = "StudyPlanner/1.0 (personal academic use)"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


# ------------------------------------------------------- scholarly articles

def openalex_lookup(query: str, doi: str | None = None) -> dict | None:
    """OpenAlex reports where a free full text lives, if one is registered.

    No API key needed. A contact email in the mailto param gets you the
    polite pool, which is faster and more reliable.
    """
    mail = config.CONTACT_EMAIL or ""
    try:
        if doi:
            url = f"https://api.openalex.org/works/doi:{doi}"
            params = {"mailto": mail} if mail else {}
        else:
            url = "https://api.openalex.org/works"
            params = {"search": query, "per-page": 1}
            if mail:
                params["mailto"] = mail
        r = httpx.get(url, params=params, headers={"User-Agent": UA}, timeout=15.0)
        if r.status_code != 200:
            return None
        data = r.json()
        w = data if doi else (data.get("results") or [None])[0]
        if not w:
            return None
        best = w.get("best_oa_location") or w.get("primary_location") or {}
        return {
            "title": w.get("title"),
            "year": w.get("publication_year"),
            "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None,
            "is_oa": bool(w.get("open_access", {}).get("is_oa")),
            "oa_status": w.get("open_access", {}).get("oa_status"),
            "url": best.get("pdf_url") or best.get("landing_page_url")
                   or w.get("open_access", {}).get("oa_url"),
            "source": (best.get("source") or {}).get("display_name"),
            "via": "OpenAlex",
        }
    except Exception:
        return None


def unpaywall_lookup(doi: str) -> dict | None:
    """Unpaywall requires an email address; it's their rate-limit identifier."""
    mail = config.CONTACT_EMAIL
    if not mail:
        return None
    try:
        r = httpx.get(f"https://api.unpaywall.org/v2/{doi}",
                      params={"email": mail}, headers={"User-Agent": UA},
                      timeout=15.0)
        if r.status_code != 200:
            return None
        d = r.json()
        loc = d.get("best_oa_location") or {}
        return {
            "title": d.get("title"),
            "year": d.get("year"),
            "doi": d.get("doi"),
            "is_oa": bool(d.get("is_oa")),
            "oa_status": d.get("oa_status"),
            "url": loc.get("url_for_pdf") or loc.get("url"),
            "source": loc.get("repository_institution") or loc.get("host_type"),
            "via": "Unpaywall",
        }
    except Exception:
        return None


def core_search(query: str) -> dict | None:
    """CORE aggregates repository-hosted copies. Needs a free API key."""
    key = config.CORE_API_KEY
    if not key:
        return None
    try:
        r = httpx.post("https://api.core.ac.uk/v3/search/works",
                       json={"q": query, "limit": 1},
                       headers={"Authorization": f"Bearer {key}", "User-Agent": UA},
                       timeout=15.0)
        if r.status_code != 200:
            return None
        res = (r.json().get("results") or [None])[0]
        if not res:
            return None
        return {
            "title": res.get("title"),
            "year": res.get("yearPublished"),
            "doi": res.get("doi"),
            "is_oa": True,
            "url": res.get("downloadUrl") or res.get("sourceFulltextUrls", [None])[0],
            "source": (res.get("publisher") or "repository"),
            "via": "CORE",
        }
    except Exception:
        return None


def find_article(title: str, doi: str | None = None) -> dict | None:
    """Try the structured sources in order of reliability."""
    doi = doi or (DOI_RE.search(title).group(0) if DOI_RE.search(title) else None)
    for fn in ((lambda: unpaywall_lookup(doi)) if doi else (lambda: None),
               lambda: openalex_lookup(title, doi),
               lambda: core_search(title)):
        hit = fn()
        if hit and hit.get("url") and hit.get("is_oa"):
            return hit
    return None


# --------------------------------------------------------------- textbooks

BOOK_SYSTEM = """\
You locate legally free copies of course texts. You search ONLY open
educational resources, public-domain archives, and lending libraries.

Return ONLY a JSON array, no prose, no fences:
[{"title": "...", "url": "...", "kind": "oer" | "public_domain" | "lending"
  | "alternative", "note": "one line on what this is and any limitation"}]

Rules:
- Return at most 4 entries, best first. An empty array is the correct and
  expected answer for a current commercial textbook — say nothing rather than
  linking something that isn't actually the text.
- "alternative" means a different, openly licensed book covering the same
  material (an OpenStax or LibreTexts equivalent). Label it honestly in the
  note: it is a substitute, not the assigned text.
- "lending" means one-copy-at-a-time borrowing. Note that it requires a free
  account and may have a waitlist.
- Never link a site whose purpose is distributing copyrighted books without
  authorization, and never link a paywalled page as if it were free.
- Prefer a specific edition page over a search results page."""


def find_book(title: str, author: str | None = None, isbn: str | None = None) -> list[dict]:
    """Search vetted OER / public-domain / lending sources via the Claude API."""
    from .. import ai

    q = title + (f" by {author}" if author else "") + (f" ISBN {isbn}" if isbn else "")
    resp = ai._client().messages.create(
        model=ai.MODEL,
        max_tokens=2000,
        system=BOOK_SYSTEM,
        messages=[{"role": "user", "content":
                   f"Find legally free ways to read: {q}"}],
        tools=[{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 5,
            # Allowlist, not blocklist — this cannot drift toward piracy.
            "allowed_domains": ALLOWED_DOMAINS,
        }],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        out = ai._json(text)
    except Exception:
        out = []
    clean = []
    for e in (out if isinstance(out, list) else [])[:4]:
        url = str(e.get("url") or "")
        if not url.startswith("http"):
            continue
        host = url.split("/")[2].lower()
        if not any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS):
            continue                      # enforce the allowlist on output too
        clean.append({
            "title": str(e.get("title") or title)[:200],
            "url": url,
            "kind": e.get("kind") if e.get("kind") in
                    ("oer", "public_domain", "lending", "alternative") else "oer",
            "note": str(e.get("note") or "")[:300],
        })
    return clean, resp


# ------------------------------------------------------------------ storage

def save(conn: sqlite3.Connection, class_id: int, kind: str, ref: str,
         found: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM materials WHERE class_id = ? AND reference = ?",
                 (class_id, ref))
    for f in found:
        conn.execute("""INSERT INTO materials
            (class_id, reference, item_kind, title, url, source, note, found_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (class_id, ref, f.get("kind", kind), f.get("title"), f.get("url"),
             f.get("via") or f.get("source"), f.get("note"), now))
    conn.commit()
    return len(found)
