# =======================================================================================
#    AMAZON SELLER CENTRAL - TOP INF ITEMS SCRAPER (V3.2.3 - FINAL LOGIC)
# =======================================================================================
# This script logs into Amazon Seller Central, navigates to the Inventory Insights
# page, and scrapes the top "Item Not Found" (INF) products.
#
# V3.2.3 Changes:
# - Wrapped the 250-pageSize change in a try/except to catch timeouts and proceed.
# - All other behavior identical to V3.2.2 (250 rows, batching, SINGLE_CARD config, etc).
# =======================================================================================

import logging
import re
import urllib.parse
from datetime import datetime
from pytz import timezone
from typing import Awaitable, Callable
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError,
    expect,
)
import os
import json
import asyncio
import pyotp
from logging.handlers import RotatingFileHandler
import aiohttp
import aiofiles
import ssl
import certifi
import argparse

# --- Basic Setup ---
LOCAL_TIMEZONE   = timezone('Europe/London')
TABLE_POLL_DELAY = 1.0    # seconds to wait after table actions
BATCH_SIZE       = 30     # max items per webhook message
SMALL_IMAGE_SIZE = 100    # px for product thumbnails
QR_CODE_SIZE     = 80     # px for QR codes

class LocalTimeFormatter(logging.Formatter):
    def converter(self, ts: float):
        return datetime.fromtimestamp(ts, LOCAL_TIMEZONE).timetuple()

def setup_logging():
    log = logging.getLogger('inf_app')
    log.setLevel(logging.INFO)
    fh = RotatingFileHandler('inf_app.log', maxBytes=10**7, backupCount=5)
    fh.setFormatter(LocalTimeFormatter('%(asctime)s %(levelname)s %(message)s'))
    ch = logging.StreamHandler()
    ch.setFormatter(LocalTimeFormatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(fh)
    log.addHandler(ch)
    return log

app_logger = setup_logging()

# --- Load config ---
try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    app_logger.critical("config.json not found. Please create it before running.")
    exit(1)

DEBUG_MODE    = config.get('debug', False)
LOGIN_URL     = config['login_url']
INF_WEBHOOK   = config.get('inf_webhook_url')
TARGET_STORE  = config['target_store']
SINGLE_CARD   = config.get('single_card', False)  # new option

# --- Paths & timeouts ---
OUTPUT_DIR      = 'output'
os.makedirs(OUTPUT_DIR, exist_ok=True)
JSON_LOG_FILE   = os.path.join(OUTPUT_DIR, 'inf_items.jsonl')
STORAGE_STATE   = 'state.json'

PAGE_TIMEOUT       = 90_000
ACTION_TIMEOUT     = 45_000
WAIT_TIMEOUT       = 45_000
WORKER_RETRY_COUNT = 3

playwright = None
browser    = None
log_lock   = asyncio.Lock()


# =======================================================================================
#         AUTHENTICATION & SESSION MANAGEMENT
# =======================================================================================

async def _save_screenshot(page: Page | None, prefix: str):
    if not page or page.is_closed():
        return
    try:
        path = os.path.join(
            OUTPUT_DIR,
            f"{prefix}_{datetime.now(LOCAL_TIMEZONE).strftime('%Y%m%d_%H%M%S')}.png"
        )
        await page.screenshot(path=path, full_page=True, timeout=15000)
        app_logger.info(f"Screenshot saved: {path}")
    except Exception as e:
        app_logger.error(f"Screenshot error: {e}")

def ensure_storage_state() -> bool:
    if not os.path.exists(STORAGE_STATE) or os.path.getsize(STORAGE_STATE) == 0:
        return False
    try:
        data = json.load(open(STORAGE_STATE))
        return isinstance(data, dict) and data.get("cookies")
    except:
        return False

async def check_if_login_needed(page: Page, test_url: str) -> bool:
    try:
        await page.goto(test_url, timeout=PAGE_TIMEOUT, wait_until="load")
        if "signin" in page.url.lower() or "/ap/" in page.url:
            app_logger.info("Session invalid, login required.")
            return True
        await expect(page.locator("#range-selector")).to_be_visible(timeout=WAIT_TIMEOUT)
        app_logger.info("Existing session still valid.")
        return False
    except Exception:
        app_logger.warning("Error verifying session; assuming login required.")
        return True

async def perform_login(page: Page) -> bool:
    app_logger.info("Starting login flow")
    try:
        await page.goto(LOGIN_URL, timeout=PAGE_TIMEOUT, wait_until="load")
        cont_input = 'input[type="submit"][aria-labelledby="continue-announce"]'
        cont_btn   = 'button:has-text("Continue shopping")'
        email_sel  = 'input#ap_email'
        await page.wait_for_selector(f"{cont_input}, {cont_btn}, {email_sel}", timeout=ACTION_TIMEOUT)
        if await page.locator(cont_input).is_visible():
            await page.locator(cont_input).click()
        elif await page.locator(cont_btn).is_visible():
            await page.locator(cont_btn).click()

        await expect(page.locator(email_sel)).to_be_visible(timeout=WAIT_TIMEOUT)
        await page.get_by_label("Email or mobile phone number").fill(config['login_email'])
        await page.get_by_label("Continue").click()
        pw = page.get_by_label("Password")
        await expect(pw).to_be_visible(timeout=WAIT_TIMEOUT)
        await pw.fill(config['login_password'])
        await page.get_by_label("Sign in").click()

        otp_sel  = 'input[id*="otp"]'
        dash_sel = "#content"
        acct_sel = 'h1:has-text("Select an account")'
        await page.wait_for_selector(f"{otp_sel}, {dash_sel}, {acct_sel}", timeout=WAIT_TIMEOUT)
        if await page.locator(otp_sel).is_visible():
            code = pyotp.TOTP(config['otp_secret_key']).now()
            await page.locator(otp_sel).fill(code)
            await page.get_by_role("button", name="Sign in").click()
            await page.wait_for_selector(f"{dash_sel}, {acct_sel}", timeout=WAIT_TIMEOUT)
        if await page.locator(acct_sel).is_visible():
            app_logger.error("Account-picker shown; unhandled.")
            await _save_screenshot(page, "login_account_picker")
            return False

        await expect(page.locator(dash_sel)).to_be_visible(timeout=WAIT_TIMEOUT)
        app_logger.info("Login successful.")
        return True

    except Exception as e:
        app_logger.critical(f"Login failed: {e}", exc_info=DEBUG_MODE)
        await _save_screenshot(page, "login_failure")
        return False

async def prime_master_session() -> bool:
    global browser
    app_logger.info("Priming master session")
    ctx = await browser.new_context()
    try:
        page = await ctx.new_page()
        if not await perform_login(page):
            return False
        await ctx.storage_state(path=STORAGE_STATE)
        app_logger.info("Saved new session state.")
        return True
    finally:
        await ctx.close()


# =======================================================================================
#                              CORE SCRAPING LOGIC
# =======================================================================================

async def log_inf_results(data: list):
    async with log_lock:
        entry = {
            'timestamp': datetime.now(LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'),
            'store': TARGET_STORE['store_name'],
            'inf_items': data
        }
        try:
            async with aiofiles.open(JSON_LOG_FILE, 'a', encoding='utf-8') as f:
                await f.write(json.dumps(entry) + '\n')
            app_logger.info("Logged INF results to file.")
        except Exception as e:
            app_logger.error(f"Log write error: {e}")

async def wait_for_table_change(
    page: Page,
    table_sel: str,
    action: Callable[[], Awaitable]
):
    first = page.locator(f"{table_sel} tr:first-child")
    text0 = ""
    if await first.count() > 0:
        text0 = await first.text_content() or ""
    await action()
    await asyncio.sleep(TABLE_POLL_DELAY)
    await page.wait_for_function(
        """([sel, init]) => {
            const el = document.querySelector(sel + ' tr:first-child');
            if (!el) return init !== '';
            return el.textContent.trim() !== init.trim();
        }""",
        arg=[table_sel, text0],
        timeout=WAIT_TIMEOUT
    )

async def scrape_inf_data(
    browser: Browser,
    store_info: dict,
    storage_state: dict,
    fetch_yesterday: bool = False
) -> list[dict] | None:
    store = store_info['store_name']
    app_logger.info(f"Opening context for '{store}'")
    ctx = await browser.new_context(storage_state=storage_state)
    page = await ctx.new_page()
    try:
        url = (
            "https://sellercentral.amazon.co.uk/snow-inventory/inventoryinsights/"
            f"?ref_=mp_home_logo_xx&cor=mmp_EU"
            f"&mons_sel_dir_mcid={store_info['merchant_id']}"
            f"&mons_sel_mkid={store_info['marketplace_id']}"
        )
        app_logger.info(f"Navigating to Inventory Insights for '{store}'")
        await page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
        await expect(page.locator("#range-selector")).to_be_visible(timeout=WAIT_TIMEOUT)
        app_logger.info("Date-picker is visible.")

        table_sel = "table.imp-table tbody"
        if fetch_yesterday:
            app_logger.info("Applying 'Yesterday' filter")
            link = page.get_by_role("link", name="Yesterday")
            await wait_for_table_change(page, table_sel, lambda: link.click())

        try:
            await expect(page.locator(f"{table_sel} tr").first).to_be_visible(timeout=20000)
        except TimeoutError:
            app_logger.info("No data rows found; exiting scrape cleanly.")
            return []

        # try/catch around pageSize change
        app_logger.info("Setting pageSize to 250 via <select>")
        try:
            await wait_for_table_change(
                page, table_sel,
                lambda: page.select_option('select[name="pageSizeDropDown"]', '250')
            )
        except TimeoutError:
            app_logger.warning(
                "Timed out waiting for pageSize change—"
                "assuming table has already loaded at 250 rows."
            )

        app_logger.info("Sorting table by 'INF Units'")
        await wait_for_table_change(page, table_sel, lambda: page.locator("#sort-3").click())

        rows = await page.locator(f"{table_sel} tr").all()
        app_logger.info(f"Found {len(rows)} rows; extracting data")

        items = []
        for r in rows:
            try:
                cells = r.locator("td")
                thumb = await cells.nth(0).locator("img").get_attribute("src") or ""
                img   = re.sub(r'\._SS\d+_\.', f'._SS{SMALL_IMAGE_SIZE}_.', thumb)
                items.append({
                    'image_url': img,
                    'sku': await cells.nth(1).locator("span").inner_text(),
                    'product_name': await cells.nth(2).locator("a span").inner_text(),
                    'inf_units': await cells.nth(3).locator("span").inner_text(),
                    'orders_impacted': await cells.nth(4).locator("span").inner_text(),
                    'inf_pct': await cells.nth(8).locator("span").inner_text(),
                })
            except Exception as e:
                app_logger.warning(f"Failed to parse row: {e}")

        app_logger.info(f"Scraped {len(items)} INF items for '{store}'")
        return items

    except Exception as e:
        app_logger.error(f"Error during scrape: {e}", exc_info=True)
        await _save_screenshot(page, "scrape_error")
        return None

    finally:
        await ctx.close()

async def post_inf_to_chat(items: list[dict]):
    if not INF_WEBHOOK:
        app_logger.warning("INF_WEBHOOK_URL not set; skipping chat post.")
        return
    if not items:
        app_logger.info("No items to post; skipping chat post.")
        return

    ts = datetime.now(LOCAL_TIMEZONE).strftime("%A %d %B, %H:%M")
    store = TARGET_STORE['store_name']

    if SINGLE_CARD:
        batches = [items[:BATCH_SIZE]]
        app_logger.info(f"SINGLE_CARD enabled: sending 1 card with up to {BATCH_SIZE} items")
    else:
        batches = [items[i:i+BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
        app_logger.info(f"SENDING {len(batches)} batch(es) of up to {BATCH_SIZE} items each")

    for idx, batch in enumerate(batches, start=1):
        widgets = [{"divider": {}}]
        for it in batch:
            code = urllib.parse.quote(it['sku'])
            qr   = f"https://api.qrserver.com/v1/create-qr-code/?size={QR_CODE_SIZE}x{QR_CODE_SIZE}&data={code}"
            widgets += [
                {
                    "columns": {
                        "columnItems": [
                            {
                                "horizontalSizeStyle": "FILL_MINIMUM_SPACE",
                                "horizontalAlignment": "CENTER",
                                "verticalAlignment": "CENTER",
                                "widgets": [{"image": {"imageUrl": qr, "altText": f"QR {it['sku']}"}}]
                            },
                            {
                                "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                                "widgets": [
                                    {"textParagraph": {"text": (
                                        f"<b>{it['product_name']}</b><br>"
                                        f"<b>SKU:</b> {it['sku']}<br>"
                                        f"<b>INF Units:</b> {it['inf_units']} ({it['inf_pct']}) | "
                                        f"<b>Orders:</b> {it['orders_impacted']}"
                                    )}},
                                    {"image": {"imageUrl": it['image_url'], "altText": it['product_name']}}
                                ]
                            }
                        ]
                    }
                },
                {"divider": {}}
            ]

        total = len(batches)
        subtitle = f"Sorted by INF Units | {ts}"
        if not SINGLE_CARD:
            subtitle += f" | batch {idx}/{total}"

        payload = {
            "cardsV2": [{
                "cardId": f"inf-report-{store.replace(' ', '-')}" + (f"-b{idx}" if not SINGLE_CARD else ""),
                "card": {
                    "header": {
                        "title": f"Top INF Items Report - {store}",
                        "subtitle": subtitle,
                        "imageUrl": "https://cdn-icons-png.flaticon.com/512/2838/2838885.png",
                        "imageType": "CIRCLE"
                    },
                    "sections": [{"widgets": widgets}]
                }
            }]
        }

        app_logger.info(f"Posting batch {idx}/{len(batches)} with {len(batch)} items")
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))
            ) as session:
                resp = await session.post(INF_WEBHOOK, json=payload)
                if resp.status == 200:
                    app_logger.info(f"Posted batch {idx}/{len(batches)} successfully")
                else:
                    text = await resp.text()
                    app_logger.error(f"Batch {idx} failed ({resp.status}): {text}")
        except Exception as e:
            app_logger.error(f"Error posting batch {idx}: {e}", exc_info=True)


# =======================================================================================
#                                  MAIN EXECUTION
# =======================================================================================

async def main(args):
    global playwright, browser

    app_logger.info("Starting INF scraper run")
    playwright = await async_playwright().start()
    browser    = await playwright.chromium.launch(headless=not DEBUG_MODE)

    # check existing session
    login_required = True
    if ensure_storage_state():
        app_logger.info("Found existing storage_state; verifying session")
        ctx  = await browser.new_context(storage_state=json.load(open(STORAGE_STATE)))
        pg   = await ctx.new_page()
        test = (
            "https://sellercentral.amazon.co.uk/snow-inventory/inventoryinsights/"
            f"?ref_=mp_home_logo_xx&cor=mmp_EU"
            f"&mons_sel_dir_mcid={TARGET_STORE['merchant_id']}"
            f"& mons_sel_mkid={TARGET_STORE['marketplace_id']}"
        )
        login_required = await check_if_login_needed(pg, test)
        await ctx.close()

    if login_required:
        app_logger.info("No valid session; logging in")
        if not await prime_master_session():
            app_logger.critical("Login failed; aborting run")
            await browser.close()
            await playwright.stop()
            return
    else:
        app_logger.info("Reusing existing session")

    storage = json.load(open(STORAGE_STATE))
    app_logger.info("Beginning data scrape")
    data    = await scrape_inf_data(browser, TARGET_STORE, storage, fetch_yesterday=args.yesterday)

    if data is None:
        app_logger.error("Scrape returned None; aborting notifications")
    elif not data:
        app_logger.info("No INF items found for the period")
    else:
        app_logger.info(f"Retrieved {len(data)} INF items; logging and notifying")
        await log_inf_results(data)
        await post_inf_to_chat(data)

    app_logger.info("Run complete; shutting down browser and Playwright")
    await browser.close()
    await playwright.stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Top INF Items from Amazon Seller Central.")
    parser.add_argument("--yesterday", action="store_true", help="Fetch yesterday's data")
    args = parser.parse_args()
    asyncio.run(main(args))
