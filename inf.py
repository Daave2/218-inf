# ==============================================================================
#    AMAZON SELLER CENTRAL - TOP INF ITEMS SCRAPER (V3.2.4 - FINAL LOGIC)
# ==============================================================================
# This script logs into Amazon Seller Central, navigates to the Inventory Insights
# page, and scrapes the top "Item Not Found" (INF) products.
# ==============================================================================

import argparse
import asyncio
import json
from playwright.async_api import async_playwright

from settings import (
    app_logger,
    DEBUG_MODE,
    TARGET_STORE,
    STORAGE_STATE,
    INVENTORY_URL,
)
from auth import ensure_storage_state, check_if_login_needed, prime_master_session
from scraper import scrape_inf_data
from notifications import log_inf_results, post_inf_to_chat, email_inf_report


async def main(args):
    app_logger.info("Starting INF scraper run")
    playwright = await async_playwright().start()
    browser    = await playwright.chromium.launch(headless=not DEBUG_MODE)

    login_required = True
    if ensure_storage_state():
        app_logger.info("Found existing storage_state; verifying session")
        ctx  = await browser.new_context(storage_state=json.load(open(STORAGE_STATE)), ignore_https_errors=True)
        pg   = await ctx.new_page()
        login_required = await check_if_login_needed(pg, INVENTORY_URL)
        await ctx.close()

    if login_required:
        app_logger.info("No valid session; logging in")
        if not await prime_master_session(browser):
            app_logger.critical("Login failed; aborting run")
            await browser.close()
            await playwright.stop()
            return
    else:
        app_logger.info("Reusing existing session")

    storage = json.load(open(STORAGE_STATE))
    app_logger.info("Beginning data scrape")
    data = await scrape_inf_data(browser, TARGET_STORE, storage, fetch_yesterday=args.yesterday)

    if data is None:
        app_logger.error("Scrape returned None; aborting notifications")
    elif not data:
        app_logger.info("No INF items found for the period")
    else:
        app_logger.info(f"Retrieved {len(data)} INF items; logging and notifying")
        await log_inf_results(data)
        await post_inf_to_chat(data)
        await email_inf_report(data)

    app_logger.info("Run complete; shutting down browser and Playwright")
    await browser.close()
    await playwright.stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Top INF Items from Amazon Seller Central.")
    parser.add_argument("--yesterday", action="store_true", help="Fetch yesterday's data")
    args = parser.parse_args()
    asyncio.run(main(args))
