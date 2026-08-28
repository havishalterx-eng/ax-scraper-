"""Website verification for lead qualification.

For website sales, "does this business have a website" is the whole question,
and Google Maps' website field answers it badly. Three ways it misleads:

1. **Social pages count as websites.** A Maps listing whose website is
   `instagram.com/somebusiness` has no site at all - it is a prime lead, and
   any tool that treats the field as a boolean drops it.
2. **Directory pages count as websites.** justdial, indiamart, sulekha,
   wikipedia, tracxn and friends are listings *about* the business, not the
   business's own site. Same story.
3. **Dead and parked domains still show.** A listing can name a domain that
   404s, times out, or resolves to a registrar's "this domain is for sale"
   page. Still a lead.

So the field is fetched and classified rather than trusted. Every row comes
back with a verdict and the evidence for it, and anything genuinely ambiguous
is labelled as needing a look rather than silently guessed - a wrong "has a
website" costs a real sales conversation.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

import httpx

# Social profiles are not websites. A business whose only web presence is a
# Facebook or Instagram page is the strongest possible lead for someone
# selling websites, so these are surfaced, not filtered away.
SOCIAL_HOSTS = (
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "wa.me", "whatsapp.com", "t.me",
    "telegram.me", "pinterest.com", "tiktok.com", "threads.net",
    "linktr.ee", "bio.link", "beacons.ai",
)

# Directory and aggregator listings - pages *about* the business, run by
# someone else. Also not a website the business owns.
DIRECTORY_HOSTS = (
    "justdial.com", "indiamart.com", "sulekha.com", "yellowpages",
    "tradeindia.com", "exportersindia.com", "wikipedia.org", "tracxn.com",
    "dnb.com", "zaubacorp.com", "yelp.com", "tripadvisor", "zomato.com",
    "swiggy.com", "practo.com", "urbanpro.com", "quikr.com", "olx.",
    "glassdoor.", "crunchbase.com", "waze.com", "foursquare.com",
    "business.site", "sites.google.com", "wordpress.com", "blogspot.",
    # Booking/storefront platforms. Seen live: a salon whose Maps "website"
    # was a store.zylu.co page - a tenant page on someone else's platform,
    # not a site the business owns.
    "zylu.co", "fresha.com", "booksy.com", "urbancompany.com", "setmore.com",
    "square.site", "linktree", "dukaan.io", "mydukaan.io", "instamojo.com",
)

# Copy that means a domain is registered but not actually a business site.
PARKED_MARKERS = (
    "domain is for sale", "buy this domain", "this domain has expired",
    "parked domain", "domain parking", "under construction",
    "coming soon", "website is coming soon", "godaddy.com/domainsearch",
    "sedoparking", "hugedomains", "afternic", "default web page",
    "index of /", "apache2 ubuntu default page", "welcome to nginx",
)

VERDICTS = {
    "LIVE": "Has a working website of its own.",
    "SOCIAL_ONLY": "No website - only a social media page. Strong lead.",
    "DIRECTORY_ONLY": "No website - only a third-party directory listing. Strong lead.",
    "PARKED": "Domain exists but shows a parked/placeholder page. Strong lead.",
    "BROKEN": "Website listed but it does not load. Strong lead.",
    "NONE": "No website listed at all. Strongest lead.",
    "UNVERIFIED": "Could not be checked - look before pitching.",
}
# Everything except LIVE and UNVERIFIED is someone worth contacting.
LEAD_VERDICTS = ("NONE", "SOCIAL_ONLY", "DIRECTORY_ONLY", "PARKED", "BROKEN")


def _host(url: str) -> str:
    try:
        # removeprefix, not lstrip: lstrip("www.") treats the argument as a
        # character set, so "wax.com" would come back as "ax.com".
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def classify_url(url: str | None) -> tuple[str, str] | None:
    """Verdict reachable from the URL alone, or None if it must be fetched."""
    if not url or not url.strip():
        return ("NONE", "No website on the listing")
    host = _host(url)
    if not host:
        return ("UNVERIFIED", "Unparseable URL")
    for social in SOCIAL_HOSTS:
        if social in host:
            return ("SOCIAL_ONLY", f"Social page only ({host})")
    for directory in DIRECTORY_HOSTS:
        if directory in host:
            return ("DIRECTORY_ONLY", f"Directory listing only ({host})")
    return None


async def _fetch_verdict(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        return ("BROKEN", "Timed out")
    except httpx.HTTPError as exc:
        return ("BROKEN", f"Did not load ({type(exc).__name__})")

    if response.status_code >= 500:
        return ("BROKEN", f"Server error {response.status_code}")
    if response.status_code >= 400:
        return ("BROKEN", f"HTTP {response.status_code}")

    # A redirect off the original host usually means the domain was sold or
    # parked; landing on a social page means the "website" is a social page.
    final_host = _host(str(response.url))
    for social in SOCIAL_HOSTS:
        if social in final_host:
            return ("SOCIAL_ONLY", f"Redirects to {final_host}")
    for directory in DIRECTORY_HOSTS:
        if directory in final_host:
            return ("DIRECTORY_ONLY", f"Redirects to {final_host}")

    body = (response.text or "")[:6000].lower()
    for marker in PARKED_MARKERS:
        if marker in body:
            return ("PARKED", f"Placeholder page ({marker!r})")

    # A page with almost no text is a shell, not a business site.
    stripped = re.sub(r"<[^>]+>", " ", body)
    if len(re.sub(r"\s+", " ", stripped).strip()) < 200:
        return ("PARKED", "Page has almost no content")

    return ("LIVE", f"Loaded OK ({response.status_code})")


async def verify_websites(rows: list[dict], concurrency: int = 8) -> list[dict]:
    """Annotate each row with website_status and website_evidence.

    Runs the checks concurrently because they are almost entirely network
    wait; serially, a 30-lead batch would take minutes for no reason.
    Redirects are followed, since the useful question is where a domain
    actually lands, not what it claims.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0),
        follow_redirects=True,
        headers={
            # Some hosts serve a bot-block page to an unfamiliar agent, which
            # would read as BROKEN and manufacture a false lead.
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            )
        },
        verify=False,  # a broken certificate is a site problem, not a lead signal
    ) as client:

        async def check(row: dict) -> dict:
            url = row.get("website")
            quick = classify_url(url)
            if quick is not None:
                row["website_status"], row["website_evidence"] = quick
                return row
            async with semaphore:
                status, evidence = await _fetch_verdict(client, url)
            row["website_status"], row["website_evidence"] = status, evidence
            return row

        return await asyncio.gather(*(check(row) for row in rows))
