"""Bulk structured extraction - the difference between a demo and a scraper.

The indexed click/type interface in state.py is right for *navigating* a page,
but it is the wrong shape for *harvesting* one. Pulling 50 products x 5 fields
through `get_text(index)` is 250 model round trips; the agent loop runs out of
steps after two products. Confirmed in a real run, not theorised.

This module does the harvest in a single call, without the model in the loop:
it finds the page's repeated record structure (the product grid, the results
list, the comment stream) by looking for sibling elements that share a
structural signature, then pulls the fields out of each one in the browser.
The model's job shrinks to "navigate to the right page, then call this once".

Field detection is deliberately pattern-based rather than site-specific: a
price is a currency-shaped string, a rating is an "N out of 5"/"N stars"
shape, the title is the longest anchor text in the record. Nothing here is
keyed to a particular retailer, so it degrades to "text + links" on a page it
doesn't recognise instead of breaking.
"""

from __future__ import annotations

from typing import Any

# Returns candidate record groups found on the page, best first. Each group is
# a repeated sibling structure - the shape a listing/search/feed page has.
EXTRACT_JS = r"""
(options) => {
  const MIN_GROUP = options && options.minGroup ? options.minGroup : 3;
  const MAX_RECORDS = options && options.limit ? options.limit : 100;
  const explicitSelector = options && options.selector ? options.selector : null;

  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity) === 0) return false;
    return true;
  };

  const cleanText = (s) => String(s || '').replace(/\s+/g, ' ').trim();

  // Structural signature: what kind of box is this, ignoring per-item noise.
  // Class lists on real sites carry both structural classes (shared by every
  // card) and state classes (only on some), so a signature built from the
  // full class list would split one grid into several groups. Sorting and
  // dropping obviously-dynamic tokens keeps siblings together.
  const signature = (el) => {
    const classes = Array.from(el.classList)
      .filter((c) => !/\d{3,}/.test(c) && c.length < 40)
      .sort()
      .join('.');
    const dataKeys = Array.from(el.attributes)
      .map((a) => a.name)
      .filter((n) => n.startsWith('data-') && n !== 'data-bmcp-idx')
      .sort()
      .join(',');
    return el.tagName + '|' + classes + '|' + dataKeys;
  };

  const collectGroups = () => {
    if (explicitSelector) {
      const nodes = Array.from(document.querySelectorAll(explicitSelector)).filter(isVisible);
      return nodes.length ? [{ score: nodes.length * 1000, nodes }] : [];
    }
    const groups = [];
    const parents = new Set();
    document.querySelectorAll('*').forEach((el) => {
      if (el.children.length >= MIN_GROUP) parents.add(el);
    });
    parents.forEach((parent) => {
      const bySig = new Map();
      Array.from(parent.children).forEach((child) => {
        if (!isVisible(child)) return;
        const sig = signature(child);
        if (!bySig.has(sig)) bySig.set(sig, []);
        bySig.get(sig).push(child);
      });
      bySig.forEach((nodes) => {
        if (nodes.length < MIN_GROUP) return;
        // A record has content. Nav bars and icon rows repeat too, so require
        // real text per item, otherwise a menu outranks the actual results.
        const textLens = nodes.map((n) => cleanText(n.innerText).length);
        const avgText = textLens.reduce((a, b) => a + b, 0) / nodes.length;
        if (avgText < 30) return;
        const withLinks = nodes.filter((n) => n.querySelector('a[href]')).length;
        const score = nodes.length * Math.min(avgText, 400) * (1 + withLinks / nodes.length);
        groups.push({ score, nodes });
      });
    });
    return groups.sort((a, b) => b.score - a.score);
  };

  const PRICE_RE = /(?:[$£€₹¥]|Rs\.?|USD|INR|EUR|GBP)\s?\d[\d,]*(?:\.\d{1,2})?/i;
  const RATING_RE = /(\d(?:\.\d)?)\s*(?:out of\s*5|\/\s*5|stars?)/i;
  const REVIEWS_RE = /^\(?\s*([\d,]{2,})\s*\)?$/;

  const parseRecord = (el) => {
    const rec = {};
    const text = cleanText(el.innerText);

    const anchors = Array.from(el.querySelectorAll('a[href]')).filter(isVisible);
    let best = null;
    anchors.forEach((a) => {
      const t = cleanText(a.innerText);
      if (t.length > 12 && (!best || t.length > cleanText(best.innerText).length)) best = a;
    });
    if (best) {
      rec.title = cleanText(best.innerText).slice(0, 300);
      rec.url = best.href;
    } else {
      const heading = el.querySelector('h1,h2,h3,h4,[role="heading"]');
      if (heading) rec.title = cleanText(heading.innerText).slice(0, 300);
      if (anchors.length) rec.url = anchors[0].href;
    }

    // Prices: take the smallest distinct currency string as the live price and
    // the largest as list price. Real listings render both (offer + M.R.P.),
    // and reading whichever appeared first gets it wrong about half the time.
    const priceMatches = [];
    (text.match(new RegExp(PRICE_RE.source, 'gi')) || []).forEach((m) => {
      const numeric = parseFloat(m.replace(/[^\d.]/g, ''));
      if (!isNaN(numeric)) priceMatches.push({ raw: m.trim(), numeric });
    });
    if (priceMatches.length) {
      const uniq = [];
      priceMatches.forEach((p) => {
        if (!uniq.some((u) => u.numeric === p.numeric)) uniq.push(p);
      });
      uniq.sort((a, b) => a.numeric - b.numeric);
      rec.price = uniq[0].raw;
      if (uniq.length > 1) rec.list_price = uniq[uniq.length - 1].raw;
    }

    const ratingMatch = text.match(RATING_RE);
    if (ratingMatch) rec.rating = ratingMatch[1];

    // Review counts. A bare-number scan is not enough: sites commonly split
    // the currency symbol into its own span, so the price "₹899" leaves a
    // span reading just "899" that looks identical to a review count. Prefer
    // an element that labels itself as ratings/reviews, and never accept a
    // number that equals a price already parsed from this same record.
    const priceNumbers = new Set(priceMatches.map((p) => p.numeric));
    const labelled = Array.from(el.querySelectorAll('[aria-label]')).find((n) =>
      /\b\d[\d,]*\s*(ratings?|reviews?)\b/i.test(n.getAttribute('aria-label') || '')
    );
    if (labelled) {
      const m = (labelled.getAttribute('aria-label') || '').match(/([\d,]+)\s*(?:ratings?|reviews?)/i);
      if (m) rec.reviews = m[1];
    }
    if (!rec.reviews) {
      const reviewEl = Array.from(el.querySelectorAll('a,span')).find((n) => {
        const t = cleanText(n.innerText);
        if (!REVIEWS_RE.test(t)) return false;
        const numeric = parseInt(t.replace(/\D/g, ''), 10);
        if (!(numeric > 9)) return false;
        if (priceNumbers.has(numeric)) return false;
        // Sitting inside a price container is the other giveaway.
        const holder = n.closest('[class*="price"],[data-a-color="price"]');
        return !holder;
      });
      if (reviewEl) rec.reviews = cleanText(reviewEl.innerText).replace(/[()]/g, '');
    }

    const img = el.querySelector('img[src]');
    if (img && isVisible(img)) rec.image = img.src;

    const lowered = text.toLowerCase();
    if (/out of stock|currently unavailable|sold out/.test(lowered)) rec.availability = 'Out of stock';
    else if (/only \d+ left|low stock/.test(lowered)) rec.availability = 'Low stock';
    else if (/in stock|add to cart|buy now/.test(lowered)) rec.availability = 'In stock';

    // Only an explicit seller label. A bare "by" matches inside ordinary
    // product titles and invents a seller that was never on the page.
    const sellerMatch = text.match(/(?:sold by|seller:|ships from and sold by)\s+([A-Z][\w&.,'-]*(?:\s+[A-Z][\w&.,'-]*){0,3})/i);
    if (sellerMatch) rec.seller = sellerMatch[1].trim();

    rec.text = text.slice(0, 400);
    return rec;
  };

  const groups = collectGroups();
  if (!groups.length) return { url: location.href, title: document.title, records: [], groupsFound: 0 };

  const chosen = groups[0].nodes.slice(0, MAX_RECORDS);
  const records = chosen.map(parseRecord).filter((r) => r.title || r.price);

  return {
    url: location.href,
    title: document.title,
    groupsFound: groups.length,
    totalInGroup: groups[0].nodes.length,
    records,
  };
}
"""


import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Sign-in or verification walls that extract_records can land on. These are
# literal signals, not a heuristic score: a URL that redirects here means the
# site will not show listings without a logged-in session or a human clearing
# a challenge. The prompt says to retry elsewhere, which burns the step budget
# on a page that cannot yield records.
_WALL_URL_SNIPPETS = (
    "?reason=lor2",  # observed Reddit old.reddit.com redirect to /login
    "/login?",       # explicit login path (e.g. reddit.com/login?dest=...)
    "/login/",
    "/signin",
    "/auth?",
    "challenges.cloudflare.com",
    "cf-im-under-attack",
    "captcha",
)
_WALL_TEXT_SNIPPETS = (
    "you've been blocked by network security",
    "log in to continue",
    "sign in to continue",
    "are you a robot",
    "verify you are human",
)


def wall_message(url: str) -> str:
    """What to tell the model when it has landed on a wall.

    Lives beside the detector so the wording cannot drift: `extract_records`
    and the page-state reader both report the same page, and two copies of
    this text would eventually disagree about what the model should do next.
    """
    return (
        f"url={url}\n\n"
        "This page is a sign-in or security wall, not a listing page. "
        "The site requires a logged-in session or human verification before it will show results. "
        "Repeating extraction, scrolling, or navigating elsewhere on this page will not help. "
        "Use a signed-in session (browser_open with persistent=True) or call request_human_help."
    )


def looks_like_wall(url: str, body_text: str) -> bool:
    lowered_url = url.lower()
    if any(snippet in lowered_url for snippet in _WALL_URL_SNIPPETS):
        return True
    lowered_text = body_text.lower()
    return any(snippet in lowered_text for snippet in _WALL_TEXT_SNIPPETS)


# session name -> the records from that session's most recent extract_records.
#
# This exists because the model is a lossy pipe for bulk data. A real run where
# the tool correctly extracted 50 records ended with only 2 in the final answer:
# the model simply would not retype 50 rows x 6 fields into a JSON block. The
# structured output a user downloads must therefore come from what the browser
# actually read, not from the model's transcription of it. The model still
# summarises and reasons over the data; it just no longer has to carry it.
LAST_EXTRACTION: dict[str, dict] = {}


def remember_extraction(session: str, records: list[dict], url: str) -> None:
    LAST_EXTRACTION[session] = {"records": records, "url": url}


def take_extraction(session: str) -> dict | None:
    return LAST_EXTRACTION.get(session)

# Query parameters real sites use to number their result pages. Ordered by how
# unambiguous they are - `p` and `start` also show up as unrelated params, so
# they are only trusted when the more explicit ones are absent.
_PAGE_PARAMS = ("page", "pg", "pageNumber", "p", "start", "offset", "from")


def next_page_url(url: str, current_index: int, page_size: int) -> str | None:
    """Build the URL for the next page of results, or None if unpaginatable.

    Two shapes exist in the wild: a page *number* (`page=2`) and a record
    *offset* (`start=20`). Guessing wrong silently reruns page 1 forever, so
    offset-style params are advanced by the number of records actually seen
    rather than by one.
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    for name in _PAGE_PARAMS:
        if name not in query:
            continue
        raw = query[name][0]
        if not raw.isdigit():
            continue
        value = int(raw)
        if name in ("start", "offset", "from"):
            query[name] = [str(value + max(page_size, 1))]
        else:
            query[name] = [str(value + 1)]
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # No page param yet: add one. Page 1 is implicit on most sites, so the
    # first explicit page to ask for is 2.
    if parsed.query or "/search" in parsed.path or "/s" == parsed.path:
        query["page"] = [str(current_index + 1)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return None


def dedupe_records(records: list[dict]) -> list[dict]:
    """Drop repeats across pages, keyed on url then title.

    Paginated sites routinely repeat sponsored rows on every page, and a site
    that ignores an out-of-range page number just re-serves page 1 - both make
    a multi-page harvest silently full of duplicates without this.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for rec in records:
        key = (rec.get("url") or "").split("?")[0] or (rec.get("title") or "")
        key = re.sub(r"\s+", " ", key).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def format_records(data: dict[str, Any], limit: int) -> str:
    """Compact, model-readable rendering.

    Returned to the agent as text rather than raw JSON because the whole point
    is to keep a 50-record harvest inside one affordable tool result; verbose
    JSON of the same rows costs several times the tokens for no extra signal.
    """
    records = data.get("records", [])
    if not records:
        return (
            f"url={data.get('url')}\n"
            "No repeated record structure found on this page. This tool looks for a "
            "list/grid of similar items - if the data you want is a single item or "
            "free text, use get_text instead."
        )

    keys: list[str] = []
    for rec in records:
        for k in rec:
            if k != "text" and k not in keys:
                keys.append(k)

    lines = [
        f"url={data.get('url')}",
        f"title={data.get('title')}",
        f"Extracted {len(records)} records (group had {data.get('totalInGroup')}, limit {limit}).",
        f"Fields present: {', '.join(keys)}",
        "",
    ]
    for i, rec in enumerate(records, 1):
        parts = [f"[{i}]"]
        for k in keys:
            if rec.get(k):
                value = str(rec[k])
                if k == "url":
                    value = value[:120]
                parts.append(f"{k}={value}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)
