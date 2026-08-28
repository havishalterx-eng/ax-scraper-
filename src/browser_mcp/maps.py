"""Google Maps lead extraction.

Maps needs its own path because the generic extractor cannot do the two things
that make Maps results useful:

1. Results live in a fixed-height scrolling panel, not paginated pages. A
   plain extract sees the first ~8 and stops; scrolling that panel takes it to
   90+. There is no `&page=2` to follow.
2. The field that actually decides a lead - does this business have a website -
   is not on the results list at all. It only appears on the individual place
   page, so each listing has to be opened.

Verified against the live site: `[role="feed"]` is the scroll container, every
result carries a real `/maps/place/` URL (so places are visited by navigation
rather than by clicking and hoping the panel updated), and the place page
exposes website, phone, address and rating through stable `data-item-id`
attributes.
"""

from __future__ import annotations

from typing import Any

from .verify import LEAD_VERDICTS

# Scrolls the results panel until it stops producing new places. Maps loads
# lazily on scroll, and stops when it runs out - detected by the count going
# flat rather than by a fixed number of scrolls.
SCROLL_FEED_JS = """
async (target) => {
  const feed = document.querySelector('[role="feed"]');
  if (!feed) return { found: 0, scrolls: 0, hadFeed: false };
  const count = () => document.querySelectorAll('a[href*="/maps/place/"]').length;
  let last = count(), flat = 0, scrolls = 0;
  for (let i = 0; i < 40; i++) {
    if (count() >= target) break;
    feed.scrollTop = feed.scrollHeight;
    scrolls++;
    await new Promise(r => setTimeout(r, 1100));
    const now = count();
    // Two flat rounds means the end of the list, not a slow network.
    if (now === last) { if (++flat >= 2) break; } else { flat = 0; }
    last = now;
  }
  return { found: count(), scrolls, hadFeed: true };
}
"""

COLLECT_PLACES_JS = """
() => {
  const seen = new Set();
  const out = [];
  document.querySelectorAll('a[href*="/maps/place/"]').forEach((a) => {
    const url = a.href;
    if (seen.has(url)) return;
    seen.add(url);
    out.push({ name: a.getAttribute('aria-label') || '', url });
  });
  return out;
}
"""

# Place-page fields. Read from data-item-id attributes, which are stable across
# locales - the visible labels are translated, these are not.
PLACE_DETAIL_JS = """
() => {
  const text = (sel) => { const e = document.querySelector(sel); return e ? e.textContent.trim() : null; };
  const aria = (sel) => { const e = document.querySelector(sel); return e ? (e.getAttribute('aria-label') || '').trim() : null; };
  const strip = (v, prefix) => (v && v.startsWith(prefix)) ? v.slice(prefix.length).trim() : v;

  const websiteEl = document.querySelector('a[data-item-id="authority"]');

  // No review count here on purpose. The obvious candidate
  // (div.F7nice span[aria-label]) is the *rating's* label - "4.9 stars" -
  // and stripping it to digits yielded "49", which looked like a plausible
  // review count and was not. The place page does not expose the count in
  // this layout; only a "Write a review" control. A missing field is
  // recoverable, an invented one silently poisons the whole dataset.
  return {
    name: text('h1'),
    website: websiteEl ? websiteEl.href : null,
    website_label: websiteEl ? text('a[data-item-id="authority"] .Io6YTe') : null,
    phone: strip(aria('button[data-item-id^="phone"]'), 'Phone:'),
    address: strip(aria('button[data-item-id="address"]'), 'Address:'),
    rating: text('div.F7nice span[aria-hidden="true"]'),
    category: text('button[jsaction*="category"]'),
  };
}
"""


def format_leads(rows: list[dict[str, Any]], notes: list[str]) -> str:
    """Renders leads grouped by how qualified they are.

    Qualified prospects come first because that is the entire output someone
    doing website sales is asking for; making them re-sort a list to find the
    businesses without a site would waste the work the verifier just did.
    Each row carries the evidence behind its verdict, so a claim like "no
    website" can be checked rather than taken on trust.
    """
    if not rows:
        return "No Maps listings found. " + " ".join(notes)

    verified = any("website_status" in r for r in rows)
    if not verified:
        # Unverified fallback: presence of a URL is all that is known, and the
        # output should not imply more than that.
        no_site = [r for r in rows if not r.get("website")]
        has_site = [r for r in rows if r.get("website")]
        lines = [
            f"Collected {len(rows)} Google Maps listings (websites NOT verified).",
            f"{len(no_site)} list no website; {len(has_site)} list one.",
            "",
        ]
        for label, group in (("NO WEBSITE LISTED", no_site), ("WEBSITE LISTED", has_site)):
            if not group:
                continue
            lines.append(f"--- {label} ({len(group)}) ---")
            for i, r in enumerate(group, 1):
                lines.append(_row_line(i, r))
            lines.append("")
        if notes:
            lines.append(" ".join(notes))
        return "\n".join(lines)

    leads = [r for r in rows if r.get("website_status") in LEAD_VERDICTS]
    live = [r for r in rows if r.get("website_status") == "LIVE"]
    unknown = [r for r in rows if r.get("website_status") == "UNVERIFIED"]

    # Strongest signal first: no site at all, then a social/directory-only
    # presence, then a domain that exists but does not work.
    order = {"NONE": 0, "SOCIAL_ONLY": 1, "DIRECTORY_ONLY": 2, "PARKED": 3, "BROKEN": 4}
    leads.sort(key=lambda r: order.get(r.get("website_status", ""), 9))

    counts: dict[str, int] = {}
    for r in rows:
        status = r.get("website_status", "UNVERIFIED")
        counts[status] = counts.get(status, 0) + 1

    lines = [
        f"Collected {len(rows)} Google Maps listings; every website was fetched and checked.",
        f"QUALIFIED LEADS: {len(leads)} of {len(rows)} have no working website of their own.",
        "Breakdown: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        "",
    ]

    if leads:
        lines.append(f"=== QUALIFIED LEADS ({len(leads)}) - pitch these ===")
        for i, r in enumerate(leads, 1):
            lines.append(_row_line(i, r, with_verdict=True))
        lines.append("")
    if unknown:
        lines.append(f"=== NEEDS A LOOK ({len(unknown)}) ===")
        for i, r in enumerate(unknown, 1):
            lines.append(_row_line(i, r, with_verdict=True))
        lines.append("")
    if live:
        lines.append(f"=== ALREADY HAVE A WORKING SITE ({len(live)}) - skip ===")
        for i, r in enumerate(live, 1):
            lines.append(_row_line(i, r, with_verdict=True))
        lines.append("")
    if notes:
        lines.append(" ".join(notes))
    return "\n".join(lines)


def _row_line(index: int, row: dict[str, Any], with_verdict: bool = False) -> str:
    parts = [f"[{index}] {row.get('name') or '(unnamed)'}"]
    if with_verdict and row.get("website_status"):
        parts.append(f"verdict={row['website_status']}")
    for key in ("rating", "phone", "address", "category"):
        if row.get(key):
            parts.append(f"{key}={row[key]}")
    if row.get("website"):
        parts.append(f"listed_site={row['website']}")
    if with_verdict and row.get("website_evidence"):
        parts.append(f"evidence={row['website_evidence']}")
    return " | ".join(parts)
