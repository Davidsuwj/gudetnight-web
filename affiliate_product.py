# -*- coding: utf-8 -*-
"""Amazon Associates product helpers for GudetNight Shorts.

Browser-side selection and posting are added incrementally; pure validation and
message formatting live here so retries stay deterministic and testable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.I)

PRODUCT_TYPE_PATTERNS = (
    (r"robot vacuum|robotic vacuum|roomba|掃地機器人", "robot_vacuum"),
    (r"mirrorless camera", "mirrorless_camera"),
    (r"portable power station", "portable_power_station"),
    (r"espresso machine", "espresso_machine"),
    (r"treadmill", "treadmill"),
    (r"projector", "projector"),
    (r"\bdrone\b", "camera_drone"),
    (r"air purifier", "air_purifier"),
    (r"dehumidifier", "dehumidifier"),
    (r"standing desk", "standing_desk"),
    (r"ergonomic (?:office )?chair", "ergonomic_office_chair"),
    (r"massage chair", "massage_chair"),
    (r"blood pressure monitor", "blood_pressure_monitor"),
    (r"hearing aid", "hearing_aid"),
    (r"\bmonitor\b|computer display", "monitor"),
    (r"\btablet\b|ipad", "tablet"),
    (r"e-?reader|kindle", "ereader"),
    (r"soundbar", "soundbar"),
    (r"security camera", "security_camera_system"),
    (r"smart (?:door )?lock", "smart_lock"),
)


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def classify_product_type(value: str) -> str:
    text = _clean(value).lower()
    for pattern, product_type in PRODUCT_TYPE_PATTERNS:
        if re.search(pattern, text, re.I):
            return product_type
    return ""


def canonical_product_type(value: str, fallback_text: str = "") -> str:
    raw = _clean(value).lower()
    broad = classify_product_type(raw.replace("_", " ")) if raw else ""
    return broad or raw or classify_product_type(fallback_text)


def extract_asin(url: str) -> str:
    text = str(url or "")
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", text, re.I)
    return match.group(1).upper() if match else ""


def parse_price(value: str) -> float | None:
    match = re.search(r"(?:US\$|\$)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _bought_count(value: str) -> int:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KkMm]?)\+?\s*bought", str(value or ""), re.I)
    if not match:
        return 0
    number = float(match.group(1))
    suffix = match.group(2).lower()
    if suffix == "k": number *= 1_000
    if suffix == "m": number *= 1_000_000
    return int(number)


def choose_candidate(
    candidates: list[dict],
    used_asins: set[str],
    min_price: float = 150,
    max_price: float = 2500,
    used_product_types: set[str] | None = None,
    product_type: str = "",
) -> dict:
    """Choose a popular, unused, mid/high-priced item from a relevant Amazon query."""
    ranked = []
    used = {str(x).upper() for x in used_asins}
    kind = canonical_product_type(product_type)
    used_types = {canonical_product_type(x) for x in (used_product_types or set()) if canonical_product_type(x)}
    if kind and kind in used_types:
        raise ValueError(f"product type already used: {kind}")
    for raw in candidates:
        asin = _clean(raw.get("asin")).upper()
        name = _clean(raw.get("name") or raw.get("title"))
        price_text = _clean(raw.get("price"))
        price_value = parse_price(price_text)
        bought = _clean(raw.get("bought"))
        badge = _clean(raw.get("badge"))
        if not ASIN_RE.fullmatch(asin) or asin in used or not name:
            continue
        if price_value is None or not (min_price <= price_value <= max_price):
            continue
        bought_count = _bought_count(bought)
        is_best = "best seller" in badge.lower() or "bestseller" in badge.lower()
        if not is_best and bought_count <= 0:
            continue
        evidence = "; ".join(x for x in [badge, bought] if x)
        score = (1_000_000_000 if is_best else 0) + bought_count
        ranked.append((score, {
            "asin": asin,
            "name": name,
            "price": price_text,
            "popularity_evidence": evidence,
            "amazon_url": f"https://www.amazon.com/dp/{asin}",
            "product_type": kind,
        }))
    if not ranked:
        raise ValueError("no popular unused Amazon product in requested price range")
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def normalize_product(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("product must be an object")
    product = {
        "asin": _clean(raw.get("asin")).upper(),
        "name": _clean(raw.get("name")),
        "amazon_url": _clean(raw.get("amazon_url")),
        "affiliate_url": _clean(raw.get("affiliate_url")),
        "price": _clean(raw.get("price")),
        "popularity_evidence": _clean(raw.get("popularity_evidence")),
        "relevance_reason": _clean(raw.get("relevance_reason")),
        "product_type": _clean(raw.get("product_type")).lower(),
    }
    if not ASIN_RE.fullmatch(product["asin"]):
        raise ValueError("invalid Amazon ASIN")
    parsed = urlparse(product["amazon_url"])
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"amazon.com", "www.amazon.com"}:
        raise ValueError("amazon_url must be an Amazon.com HTTPS product URL")
    url_asin = extract_asin(product["amazon_url"])
    if url_asin != product["asin"]:
        raise ValueError("amazon_url ASIN does not match product ASIN")
    aff = urlparse(product["affiliate_url"])
    if aff.scheme != "https" or aff.netloc.lower() not in {"amzn.to", "www.amzn.to"}:
        raise ValueError("affiliate_url must be copied from Amazon SiteStripe")
    if not product["name"]:
        raise ValueError("product name required")
    if not product["popularity_evidence"]:
        raise ValueError("popularity evidence required")
    if not product["relevance_reason"]:
        raise ValueError("relevance reason required")
    product["product_type"] = canonical_product_type(product["product_type"], product["name"])
    return product


def affiliate_comment_text(raw: dict) -> str:
    product = normalize_product(raw)
    short_name = re.split(r"\s*[|,]\s*", product["name"], maxsplit=1)[0].strip()
    return f"{short_name}\n{product['affiliate_url']}"


def extract_video_id(url: str) -> str:
    match = re.search(r"(?:shorts/|youtu\.be/|[?&]v=)([A-Za-z0-9_-]{11})(?:[?&#/]|$)", str(url or ""))
    return match.group(1) if match else ""


def comment_fingerprint(video_id: str, affiliate_url: str) -> str:
    payload = f"{_clean(video_id)}\n{_clean(affiliate_url)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_used_asins(history_path: Path) -> set[str]:
    return load_history_dedup(history_path)[0]


def load_history_dedup(history_path: Path) -> tuple[set[str], set[str]]:
    used: set[str] = set()
    product_types: set[str] = set()
    if not history_path.exists():
        return used, product_types
    for line in history_path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        asin = _clean(item.get("asin")).upper()
        if ASIN_RE.fullmatch(asin):
            used.add(asin)
        fallback_text = " ".join(str(item.get(k) or "") for k in (
            "product", "name", "title", "source_evidence", "search_query"
        ))
        product_type = canonical_product_type(item.get("product_type"), fallback_text)
        if product_type:
            product_types.add(product_type)
    return used, product_types


def reserve_product(history_path: Path, product: dict, search_query: str) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(product)
    record.update({
        "status": "selected_for_shorts",
        "search_query": search_query,
        "selected_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
    })
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def select_amazon_product(
    search_query: str,
    relevance_reason: str,
    history_path: Path,
    min_price: float = 150,
    max_price: float = 2500,
    product_type: str = "",
    cdp_url: str | None = None,
) -> dict:
    """Search Amazon.com, rank popularity evidence, and copy a real SiteStripe link."""
    from playwright.sync_api import sync_playwright

    query = _clean(search_query)
    reason = _clean(relevance_reason)
    if not query or not reason:
        raise ValueError("search query and relevance reason are required")
    kind = canonical_product_type(product_type, query)
    if not kind:
        raise ValueError("product_type is required when it cannot be inferred from search query")
    if not cdp_url:
        import worker
        worker.ensure_cdp_chrome()
        cdp_url = worker.validated_cdp_url()
    if not cdp_url:
        raise RuntimeError("No validated visible Chrome CDP endpoint")

    used, used_types = load_history_dedup(history_path)
    if kind in used_types:
        raise ValueError(f"product type already used: {kind}")
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=30000)
        context = browser.contexts[0]
        search_page = context.new_page()
        product_page = None
        try:
            search_url = (
                "https://www.amazon.com/s?k=" + quote_plus(query)
                + "&s=exact-aware-popularity-rank&language=en_US&currency=USD"
            )
            search_page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            search_page.wait_for_timeout(7000)
            body = search_page.locator("body").inner_text(timeout=20000)
            if "Enter the characters you see below" in body:
                raise RuntimeError("Amazon CAPTCHA blocked product search")
            cards = search_page.locator('[data-component-type="s-search-result"]')
            candidates: list[dict] = []
            for idx in range(min(cards.count(), 40)):
                card = cards.nth(idx)
                asin = _clean(card.get_attribute("data-asin")).upper()
                if not ASIN_RE.fullmatch(asin):
                    continue
                text = _clean(card.inner_text(timeout=10000))
                titles = card.locator("h2 span")
                if not titles.count():
                    titles = card.locator("h2")
                name = _clean(titles.first.text_content(timeout=5000)) if titles.count() else ""
                prices = card.locator(".a-price .a-offscreen")
                price = _clean(prices.first.text_content(timeout=5000)) if prices.count() else ""
                bought_match = re.search(r"([0-9]+(?:\.[0-9]+)?\s*[KkMm]?\+\s*bought in past month)", text, re.I)
                badge = "Best Seller" if re.search(r"\bBest Seller\b", text, re.I) else ""
                candidates.append({
                    "asin": asin,
                    "name": name,
                    "price": price,
                    "bought": _clean(bought_match.group(1)) if bought_match else "",
                    "badge": badge,
                })
            selected = choose_candidate(
                candidates,
                used,
                min_price=min_price,
                max_price=max_price,
                used_product_types=used_types,
                product_type=kind,
            )

            product_page = context.new_page()
            product_page.goto(selected["amazon_url"], wait_until="domcontentloaded", timeout=60000)
            product_page.wait_for_timeout(6000)
            if not product_page.locator("#amzn-ss-text-link").count():
                raise RuntimeError("Amazon SiteStripe is unavailable on selected product page")
            title_loc = product_page.locator("span#productTitle").first
            if title_loc.count():
                selected["name"] = _clean(title_loc.inner_text(timeout=10000))
            price_loc = product_page.locator("#corePrice_feature_div .a-offscreen, .a-price .a-offscreen").first
            if price_loc.count():
                selected["price"] = _clean(price_loc.text_content(timeout=5000)) or selected["price"]

            product_page.locator("#amzn-ss-text-link").click(timeout=15000)
            product_page.wait_for_timeout(1800)
            modal = product_page.locator("#amzn-ss-popover-text-preload-content-container")
            if not modal.is_visible():
                raise RuntimeError("SiteStripe text-link dialog did not open")
            short_label = modal.get_by_text(re.compile(r"Short Link|Short URL", re.I), exact=False)
            if not short_label.count():
                raise RuntimeError("SiteStripe short-link option unavailable")
            short_label.first.click()
            product_page.wait_for_timeout(900)
            copy_btn = product_page.get_by_text(re.compile(r"Copy affiliate link", re.I), exact=True)
            if copy_btn.count():
                copy_btn.first.click(timeout=10000)
            elif product_page.locator("#amzn-ss-copy-affiliate-link-btn").count():
                product_page.locator("#amzn-ss-copy-affiliate-link-btn").click(timeout=10000)
            else:
                raise RuntimeError("SiteStripe Copy affiliate link button unavailable")
            product_page.wait_for_timeout(500)
            affiliate_url = product_page.evaluate("navigator.clipboard.readText()")
            selected["affiliate_url"] = _clean(affiliate_url)
            selected["relevance_reason"] = reason
            selected = normalize_product(selected)
            reserve_product(history_path, selected, query)
            return selected
        finally:
            if product_page is not None:
                product_page.close()
            search_page.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Select a verified Amazon affiliate product")
    parser.add_argument("select", nargs="?")
    parser.add_argument("--query", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--history",
        default=str(Path.home() / ".gudetnight" / "product_history.jsonl"),
    )
    parser.add_argument("--min-price", type=float, default=150)
    parser.add_argument("--max-price", type=float, default=2500)
    parser.add_argument("--product-type", default="")
    parser.add_argument("--cdp", default="")
    args = parser.parse_args()
    result = select_amazon_product(
        args.query,
        args.reason,
        Path(args.history),
        min_price=args.min_price,
        max_price=args.max_price,
        product_type=args.product_type,
        cdp_url=args.cdp or None,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
