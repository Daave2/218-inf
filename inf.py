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
    ENABLE_SUPABASE_UPLOAD,
    EMAIL_REPORT,
    LOGIN_RETRIES,
    SCRAPE_RETRIES,
    ENABLE_STOCK_LOOKUP,
)
from auth import (
    ensure_storage_state,
    check_if_login_needed,
    login_with_retries,
)
from scraper import scrape_with_retries
from notifications import (
    log_inf_results,
    post_inf_to_chat,
    email_inf_report,
    filter_items_posted_today,
)
from database import create_investigation_from_scrape
from stock_checker import enrich_items_with_stock_data


async def main(args):
    app_logger.info("Starting INF scraper run")
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=not DEBUG_MODE)

    login_required = True
    if ensure_storage_state():
        app_logger.info("Found existing storage_state; verifying session")
        ctx = await browser.new_context(
            storage_state=json.load(open(STORAGE_STATE)), ignore_https_errors=True
        )
        pg = await ctx.new_page()
        login_required = await check_if_login_needed(pg, INVENTORY_URL)
        await ctx.close()

    if login_required:
        app_logger.info("No valid session; logging in")
        if not await login_with_retries(browser, LOGIN_RETRIES):
            app_logger.critical("Login failed; aborting run")
            await browser.close()
            await playwright.stop()
            return
    else:
        app_logger.info("Reusing existing session")

    storage = json.load(open(STORAGE_STATE))
    app_logger.info("Beginning data scrape")
    fetch_yesterday = args.yesterday or EMAIL_REPORT
    data = await scrape_with_retries(
        browser,
        TARGET_STORE,
        storage,
        fetch_yesterday=fetch_yesterday,
        attempts=SCRAPE_RETRIES,
    )

    if data is None:
        app_logger.error("Scrape returned None; aborting notifications")
    elif not data:
        app_logger.info("No INF items found for the period")
    else:
        app_logger.info(f"Retrieved {len(data)} INF items; processing...")

        data = await filter_items_posted_today(data)

        if not data:
            app_logger.info(
                "All INF items retrieved have already been posted today. "
                "Skipping downstream notifications."
            )
        else:
            if ENABLE_STOCK_LOOKUP:
                app_logger.info("Stock lookup enabled. Enriching items...")
                data = await enrich_items_with_stock_data(data)
            else:
                app_logger.info("Stock lookup disabled. Skipping enrichment.")

            if ENABLE_SUPABASE_UPLOAD and not EMAIL_REPORT:
                app_logger.info("Supabase upload is enabled. Creating investigation...")
                await create_investigation_from_scrape(data)
            else:
                app_logger.info(
                    "Supabase upload disabled or email mode active. Skipping database update."
                )

            # Run existing logging and notification steps
            await log_inf_results(data)
            await post_inf_to_chat(data)
            await email_inf_report(data)

    app_logger.info("Run complete; shutting down browser and Playwright")
    await browser.close()
    await playwright.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape Top INF Items from Amazon Seller Central."
    )
    parser.add_argument(
        "--yesterday", action="store_true", help="Fetch yesterday's data"
    )
    args = parser.parse_args()
    asyncio.run(main(args))
