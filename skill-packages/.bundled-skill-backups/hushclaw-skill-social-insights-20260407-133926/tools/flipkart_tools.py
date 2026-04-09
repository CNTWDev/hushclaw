"""Flipkart tools — HTTP scraping with httpx + BeautifulSoup4.

No authentication required. Flipkart has no public product review API.
Uses structured HTML parsing to extract product listings and reviews.

Note: Flipkart may update their CSS class names. If scraping breaks,
the tool will return a descriptive error rather than silently failing.
"""
from __future__ import annotations

import json
import re
import time

from hushclaw.tools.base import ToolResult, tool

_BASE = "https://www.flipkart.com"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _http_get(url: str, params: dict | None = None) -> tuple[int, str]:
    """GET a Flipkart page and return (status_code, html)."""
    try:
        import httpx
    except ImportError:
        return -1, "httpx is not installed. Run: pip install httpx"
    try:
        resp = httpx.get(url, params=params, headers=_HEADERS, timeout=20, follow_redirects=True)
        return resp.status_code, resp.text
    except httpx.RequestError as e:
        return -1, f"Network error: {e}"


def _bs(html: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    return BeautifulSoup(html, "html.parser")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_price(text: str) -> str:
    """Normalise a price string like '₹1,299' → '₹1,299'."""
    return _clean(text).replace("\u20b9", "₹")


@tool(description=(
    "Search Flipkart for products and return a list with name, price, rating, "
    "review count, and product URL. No authentication required."
))
def flipkart_search(
    query: str,
    limit: int = 10,
) -> ToolResult:
    """Search Flipkart products by keyword."""
    if not query.strip():
        return ToolResult.error("query cannot be empty")
    limit = max(1, min(limit, 50))

    try:
        from bs4 import BeautifulSoup  # noqa: F401
    except ImportError:
        return ToolResult.error(
            "beautifulsoup4 is not installed. Run: pip install beautifulsoup4"
        )

    status, html = _http_get(f"{_BASE}/search", {"q": query})
    if status == -1:
        return ToolResult.error(html)
    if status != 200:
        return ToolResult.error(f"Flipkart returned HTTP {status}")

    soup = _bs(html)
    if soup is None:
        return ToolResult.error("Failed to parse HTML (beautifulsoup4 not available)")

    products = []

    # Flipkart renders search results as anchor tags with data-id attributes
    # Try multiple known selectors to handle Flipkart layout variations
    containers = (
        soup.select("div[data-id]")
        or soup.select("div._1AtVbE")
        or soup.select("div._13oc-S")
    )

    for container in containers:
        if len(products) >= limit:
            break

        # Product name
        name_el = (
            container.select_one("div._4rR01T")
            or container.select_one("a.s1Q9rs")
            or container.select_one("a.IRpwTa")
            or container.select_one("[class*='_4rR01T']")
        )
        if not name_el:
            continue
        name = _clean(name_el.get_text())
        if not name:
            continue

        # Price
        price_el = (
            container.select_one("div._30jeq3")
            or container.select_one("div._1_WHN1")
            or container.select_one("[class*='30jeq3']")
        )
        price = _extract_price(price_el.get_text()) if price_el else "N/A"

        # Rating
        rating_el = (
            container.select_one("div._3LWZlK")
            or container.select_one("[class*='_3LWZlK']")
        )
        rating = _clean(rating_el.get_text()) if rating_el else "N/A"

        # Review count
        review_el = (
            container.select_one("span._2_R_DZ")
            or container.select_one("span._13vcmD")
            or container.select_one("[class*='_2_R_DZ']")
        )
        review_count = _clean(review_el.get_text()) if review_el else "N/A"

        # Product URL — find the nearest anchor
        link_el = container.select_one("a[href]")
        product_url = ""
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("/"):
                product_url = _BASE + href
            elif href.startswith("http"):
                product_url = href

        products.append({
            "name": name,
            "price": price,
            "rating": rating,
            "review_count": review_count,
            "url": product_url,
        })

    if not products:
        return ToolResult.error(
            "No products found. Flipkart may have updated their HTML structure. "
            "Try a different query or check if the page is accessible."
        )

    return ToolResult.ok(json.dumps({
        "query": query,
        "count": len(products),
        "products": products,
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Get customer reviews for a Flipkart product. "
    "product_url: the full Flipkart product page URL. "
    "The tool will navigate to the product's reviews page automatically. "
    "page: reviews page number (1-based, default 1). "
    "No authentication required."
))
def flipkart_reviews(
    product_url: str,
    page: int = 1,
    limit: int = 20,
) -> ToolResult:
    """Scrape customer reviews from a Flipkart product page."""
    product_url = product_url.strip()
    if not product_url:
        return ToolResult.error("product_url cannot be empty")
    if "flipkart.com" not in product_url:
        return ToolResult.error("URL must be a Flipkart product URL")
    page = max(1, page)
    limit = max(1, min(limit, 100))

    try:
        from bs4 import BeautifulSoup  # noqa: F401
    except ImportError:
        return ToolResult.error(
            "beautifulsoup4 is not installed. Run: pip install beautifulsoup4"
        )

    # Construct the reviews URL:
    # Flipkart review pages follow /product-reviews/ path pattern
    # If the URL already points to a product page, derive the review URL
    review_url = product_url
    if "/product-reviews/" not in product_url:
        # Replace /p/ with /product-reviews/ in the path
        review_url = re.sub(r"/p/", "/product-reviews/", product_url, count=1)
        if review_url == product_url:
            # Fallback: append /product-reviews to the base product path
            base = product_url.split("?")[0].rstrip("/")
            review_url = base.rsplit("/", 1)[0] + "/product-reviews/" + base.rsplit("/", 1)[-1]

    params: dict = {}
    if page > 1:
        params["page"] = page

    time.sleep(0.5)  # polite delay
    status, html = _http_get(review_url, params or None)
    if status == -1:
        return ToolResult.error(html)
    if status == 404:
        return ToolResult.error(
            "Reviews page not found. Try passing the direct product URL from Flipkart search results."
        )
    if status != 200:
        return ToolResult.error(f"Flipkart returned HTTP {status}")

    soup = _bs(html)
    if soup is None:
        return ToolResult.error("Failed to parse HTML")

    reviews = []

    # Review containers
    review_blocks = (
        soup.select("div._1AtVbE div.col._2wzgFH")
        or soup.select("div[class*='col _2wzgFH']")
        or soup.select("div._27M-vq")  # older layout
        or soup.select("div.review-container")
    )

    for block in review_blocks:
        if len(reviews) >= limit:
            break

        # Rating (star value)
        rating_el = block.select_one("div._3LWZlK") or block.select_one("[class*='_3LWZlK']")
        rating = _clean(rating_el.get_text()) if rating_el else "N/A"

        # Review title
        title_el = block.select_one("p._2-N8zT") or block.select_one("[class*='_2-N8zT']")
        title = _clean(title_el.get_text()) if title_el else ""

        # Review text body
        body_el = (
            block.select_one("div.t-ZTKy")
            or block.select_one("div._6K-7Co")
            or block.select_one("[class*='t-ZTKy']")
            or block.select_one("[class*='_6K-7Co']")
        )
        if body_el:
            # Remove "READ MORE" spans
            for span in body_el.select("span._2jwP0J, span.read-more"):
                span.decompose()
            body = _clean(body_el.get_text())
        else:
            body = ""

        if not body and not title:
            continue

        # Reviewer name
        reviewer_el = (
            block.select_one("p._2sc7ZR")
            or block.select_one("[class*='_2sc7ZR']")
        )
        reviewer = _clean(reviewer_el.get_text()) if reviewer_el else ""

        # Review date
        date_el = (
            block.select_one("p._2sc7ZR + p")
            or block.select_one("span._2_R_DZ span")
            or block.select_one("[class*='_2sc7ZR'] ~ p")
        )
        date = _clean(date_el.get_text()) if date_el else ""

        # Helpful count
        helpful_el = block.select_one("span._3c3OHP") or block.select_one("[class*='_3c3OHP']")
        helpful = _clean(helpful_el.get_text()) if helpful_el else ""

        reviews.append({
            "rating": rating,
            "title": title,
            "review": body,
            "reviewer": reviewer,
            "date": date,
            "helpful_count": helpful,
        })

    if not reviews:
        return ToolResult.error(
            "No reviews found on this page. The product may have no reviews, "
            "or Flipkart may have updated their HTML structure. "
            "Ensure the URL is a valid Flipkart product page."
        )

    return ToolResult.ok(json.dumps({
        "product_url": product_url,
        "reviews_page": page,
        "fetched_reviews": len(reviews),
        "reviews": reviews,
    }, ensure_ascii=False, indent=2))
