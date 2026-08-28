"""Prebuilt task templates - the starting points a person actually picks from.

A blank prompt box is the hardest part of using this tool: the difference
between a run that works in 3 steps and one that loops until the budget dies
is almost entirely how the task was phrased. These templates encode the
phrasings that were verified to work against the real sites - land directly on
a results URL, state the fields wanted, name a record count - so the good path
is the default rather than something you have to discover.

Every prompt here has been shaped around what the tools actually do well:
point at a listing URL, let `extract_records` harvest and paginate. Nothing
here is a stub - each one creates a real agent that runs.
"""

from __future__ import annotations

CATEGORIES = ["Lead generation", "E-commerce", "Social media", "Research", "Jobs"]

TEMPLATES: list[dict] = [
    {
        "id": "amazon-search",
        "name": "Amazon Product Search Scraper",
        "category": "E-commerce",
        "site": "Amazon",
        "accent": "#FF9900",
        "description": "Collect products for any search term with price, list price, rating, review count, availability and source URL.",
        "inputs": [
            {"key": "query", "label": "Search term", "default": "wireless headphones"},
            {"key": "domain", "label": "Amazon site", "default": "amazon.in"},
            {"key": "count", "label": "How many products", "default": "50"},
        ],
        "prompt": "Go to https://www.{domain}/s?k={query_plus} and call extract_records with limit {count} to collect the products, including price, rating, review count, availability and source URL.",
        "est_steps": "2-4 steps",
    },
    {
        "id": "amazon-bestsellers",
        "name": "Amazon Best Sellers Scraper",
        "category": "E-commerce",
        "site": "Amazon",
        "accent": "#FF9900",
        "description": "Harvest a Best Sellers category page - the current top-ranked products with prices and ratings.",
        "inputs": [
            {"key": "domain", "label": "Amazon site", "default": "amazon.in"},
            {"key": "count", "label": "How many products", "default": "50"},
        ],
        "prompt": "Go to https://www.{domain}/gp/bestsellers/ and call extract_records with limit {count} to collect the best selling products with their price, rating and source URL.",
        "est_steps": "2-4 steps",
    },
    {
        "id": "maps-businesses",
        "name": "Google Maps Business Listings",
        "category": "Lead generation",
        "site": "Google Maps",
        "accent": "#34A853",
        "description": "Build a local business list with phone, address, rating and website - and see at a glance which businesses have no website at all.",
        "inputs": [
            {"key": "business", "label": "Business type", "default": "dental clinics"},
            {"key": "location", "label": "Location", "default": "Hyderabad"},
            {"key": "count", "label": "How many businesses", "default": "30"},
        ],
        "prompt": "Call maps_leads with query \"{business} in {location}\" and limit {count} to collect the businesses, including which ones have no website.",
        "est_steps": "1-2 steps",
    },
    {
        "id": "reddit-posts",
        "name": "Reddit Posts Scraper",
        "category": "Social media",
        "site": "Reddit",
        "accent": "#FF4500",
        "description": "Pull the current posts from any subreddit with titles, scores, comment counts and permalinks.",
        "inputs": [
            {"key": "subreddit", "label": "Subreddit", "default": "programming"},
            {"key": "sort", "label": "Sort by", "default": "hot"},
            {"key": "count", "label": "How many posts", "default": "30"},
        ],
        "prompt": "Go to https://old.reddit.com/r/{subreddit}/{sort}/ and call extract_records with limit {count} to collect the post titles, scores, comment counts and links. Use old.reddit.com - its markup is far easier to extract than the new interface.",
        "est_steps": "2-5 steps",
    },
    {
        "id": "hn-frontpage",
        "name": "Hacker News Front Page",
        "category": "Research",
        "site": "Hacker News",
        "accent": "#FF6600",
        "description": "Grab the current front page with titles, points, comment counts and outbound links.",
        "inputs": [
            {"key": "count", "label": "How many stories", "default": "30"},
        ],
        "prompt": "Go to https://news.ycombinator.com/ and call extract_records with limit {count} to collect the story titles, points, comment counts and links.",
        "est_steps": "2-3 steps",
    },
    {
        "id": "ebay-search",
        "name": "eBay Search Scraper",
        "category": "E-commerce",
        "site": "eBay",
        "accent": "#E53238",
        "description": "Collect eBay listings for a search term with prices, conditions and source URLs.",
        "inputs": [
            {"key": "query", "label": "Search term", "default": "mechanical keyboard"},
            {"key": "count", "label": "How many listings", "default": "50"},
        ],
        "prompt": "Go to https://www.ebay.com/sch/i.html?_nkw={query_plus} and call extract_records with limit {count} to collect the listing titles, prices and source URLs.",
        "est_steps": "2-4 steps",
    },
    {
        "id": "producthunt",
        "name": "Product Hunt Launches",
        "category": "Research",
        "site": "Product Hunt",
        "accent": "#DA552F",
        "description": "Track today's launches with names, taglines, upvote counts and links.",
        "inputs": [
            {"key": "count", "label": "How many products", "default": "25"},
        ],
        "prompt": "Go to https://www.producthunt.com/ and call extract_records with limit {count} to collect the product names, taglines, upvotes and links.",
        "est_steps": "2-5 steps",
    },
    {
        "id": "yc-jobs",
        "name": "Y Combinator Job Board",
        "category": "Jobs",
        "site": "Work at a Startup",
        "accent": "#FF6600",
        "description": "Collect startup job postings with role titles, companies and links.",
        "inputs": [
            {"key": "role", "label": "Role keyword", "default": "engineer"},
            {"key": "count", "label": "How many jobs", "default": "40"},
        ],
        "prompt": "Go to https://www.ycombinator.com/jobs/role/{role} and call extract_records with limit {count} to collect the job titles, company names, locations and links.",
        "est_steps": "2-5 steps",
    },
    {
        "id": "google-results",
        "name": "Google Search Results",
        "category": "Research",
        "site": "Google",
        "accent": "#4285F4",
        "description": "Capture the organic results for a query - titles, URLs and snippets.",
        "inputs": [
            {"key": "query", "label": "Search query", "default": "best crm for small business"},
            {"key": "count", "label": "How many results", "default": "20"},
        ],
        "prompt": "Go to https://www.google.com/search?q={query_plus}&num=30 and call extract_records with limit {count} to collect the result titles, links and snippets. If a consent or CAPTCHA page appears, call request_human_help so a person can clear it.",
        "est_steps": "2-6 steps",
    },
    {
        "id": "indeed-jobs",
        "name": "Indeed Job Listings",
        "category": "Jobs",
        "site": "Indeed",
        "accent": "#2557A7",
        "description": "Build a job list for a role and location with titles, companies, salaries and links.",
        "inputs": [
            {"key": "role", "label": "Job title", "default": "data analyst"},
            {"key": "location", "label": "Location", "default": "Hyderabad"},
            {"key": "count", "label": "How many jobs", "default": "40"},
        ],
        "prompt": "Go to https://in.indeed.com/jobs?q={role_plus}&l={location_plus} and call extract_records with limit {count} to collect job titles, company names, locations, salaries where shown, and links. If a verification page appears, call request_human_help.",
        "est_steps": "2-6 steps",
    },
    {
        "id": "yelp-businesses",
        "name": "Yelp Business Finder",
        "category": "Lead generation",
        "site": "Yelp",
        "accent": "#D32323",
        "description": "Find local businesses with ratings, review counts, categories and links.",
        "inputs": [
            {"key": "business", "label": "Business type", "default": "coffee shops"},
            {"key": "location", "label": "Location", "default": "San Francisco"},
            {"key": "count", "label": "How many businesses", "default": "30"},
        ],
        "prompt": "Go to https://www.yelp.com/search?find_desc={business_plus}&find_loc={location_plus} and call extract_records with limit {count} to collect business names, ratings, review counts and links.",
        "est_steps": "2-6 steps",
    },
    {
        "id": "custom",
        "name": "Blank agent",
        "category": "Research",
        "site": "Any site",
        "accent": "#FF4D0A",
        "description": "Start from nothing and describe the job yourself. Land on the page that already shows the data, and say which fields you want.",
        "inputs": [
            {"key": "url", "label": "Starting URL", "default": "https://example.com"},
            {"key": "goal", "label": "What to collect", "default": "the records on the page with their key fields"},
            {"key": "count", "label": "How many records", "default": "30"},
        ],
        "prompt": "Go to {url} and call extract_records with limit {count} to collect {goal}.",
        "est_steps": "varies",
    },
]

_BY_ID = {t["id"]: t for t in TEMPLATES}


def list_templates(category: str | None = None, search: str | None = None) -> list[dict]:
    items = TEMPLATES
    if category and category != "All":
        items = [t for t in items if t["category"] == category]
    if search:
        needle = search.lower().strip()
        items = [
            t
            for t in items
            if needle in t["name"].lower()
            or needle in t["description"].lower()
            or needle in t["site"].lower()
        ]
    return items


def get_template(template_id: str) -> dict | None:
    return _BY_ID.get(template_id)


def render_prompt(template_id: str, values: dict) -> str | None:
    """Fill a template's prompt with the caller's values.

    Every input also gets a `<key>_plus` form with spaces as `+`, because most
    of these prompts drop the value straight into a query string - building
    that in the template rather than hoping the model URL-encodes correctly is
    the difference between landing on the results page and landing on a 404.
    """
    template = get_template(template_id)
    if template is None:
        return None
    filled: dict[str, str] = {}
    for spec in template["inputs"]:
        key = spec["key"]
        raw = str(values.get(key) or spec["default"]).strip()
        filled[key] = raw
        filled[f"{key}_plus"] = raw.replace(" ", "+")
    try:
        return template["prompt"].format(**filled)
    except KeyError:
        return template["prompt"]
