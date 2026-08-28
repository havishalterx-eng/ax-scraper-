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
    """Renders leads as compact text, no-website ones first.

    Ordered that way because for website-sales prospecting the businesses
    without a site are the entire point - burying them under the ones that
    already have one makes the caller re-sort a result they asked for.
    """
    if not rows:
        return "No Maps listings found. " + " ".join(notes)

    no_site = [r for r in rows if not r.get("website")]
    has_site = [r for r in rows if r.get("website")]

    lines = [
        f"Collected {len(rows)} Google Maps listings.",
        f"{len(no_site)} have NO website (the sales targets); {len(has_site)} already have one.",
        "",
    ]
    for label, group in (("NO WEBSITE", no_site), ("HAS WEBSITE", has_site)):
        if not group:
            continue
        lines.append(f"--- {label} ({len(group)}) ---")
        for i, r in enumerate(group, 1):
            parts = [f"[{i}] {r.get('name') or '(unnamed)'}"]
            for key in ("rating", "phone", "address", "category"):
                if r.get(key):
                    parts.append(f"{key}={r[key]}")
            if r.get("website"):
                parts.append(f"website={r['website']}")
            lines.append(" | ".join(parts))
        lines.append("")
    if notes:
        lines.append(" ".join(notes))
    return "\n".join(lines)
