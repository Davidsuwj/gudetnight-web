"""CDP Chrome connection and page helpers."""
import asyncio

from .config import CDP_URL


async def _cleanup_unresponsive_safe_targets():
    """Best-effort retry hook.

    Target deletion is intentionally disabled in the reusable public module: an
    authenticated tab belongs to the user and must not be closed blindly. Local
    deployments may replace this hook with allow-listed stale-target cleanup.
    """
    return 0


async def connect(playwright):
    """Connect to the existing visible CDP Chrome, cleaning/retrying once."""
    try:
        browser = await playwright.chromium.connect_over_cdp(CDP_URL, timeout=30000)
    except Exception as first_error:
        cleaned = await _cleanup_unresponsive_safe_targets()
        print(f"[WARN] Initial CDP connection failed; cleaned={cleaned}; retrying once: {first_error}")
        await asyncio.sleep(0.2)
        browser = await playwright.chromium.connect_over_cdp(CDP_URL, timeout=30000)
    print("[OK] Connected to CDP Chrome")
    return browser


def primary_context(browser):
    """Return the first existing browser context."""
    return browser.contexts[0] if browser.contexts else None


async def find_or_create_page(browser, url_keyword):
    """Reuse an app tab or create one without closing any user-owned tabs."""
    context = primary_context(browser)
    if context is None:
        context = await browser.new_context()
    for page in context.pages:
        if url_keyword in page.url:
            print(f"[OK] Found existing tab: {url_keyword}")
            return page
    print(f"[INFO] Creating new tab for: {url_keyword}")
    return await context.new_page()
