# =======================================================================================
#    AMAZON SELLER CENTRAL - TOP INF ITEMS SCRAPER (V1.8 - FINAL LAYOUT & SIZE)
# =======================================================================================
# This script logs into Amazon Seller Central, navigates to the Inventory Insights
# page, scrapes the table of top "Item Not Found" (INF) products for the day,
# and sends a formatted report to a Google Chat webhook.
#
# V1.8 Changes:
# - Adjusted the requested QR code size to 100x100 for better visual balance.
# =======================================================================================

import logging
import re
import urllib.parse
from datetime import datetime
from pytz import timezone
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

# --- Basic Setup ---
LOCAL_TIMEZONE = timezone('Europe/London')

class LocalTimeFormatter(logging.Formatter):
    def converter(self, ts: float):
        dt = datetime.fromtimestamp(ts, LOCAL_TIMEZONE)
        return dt.timetuple()

def setup_logging():
    app_logger = logging.getLogger('inf_app')
    app_logger.setLevel(logging.INFO)
    app_file = RotatingFileHandler('inf_app.log', maxBytes=10**7, backupCount=5)
    fmt = LocalTimeFormatter('%(asctime)s %(levelname)s %(message)s')
    app_file.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    app_logger.addHandler(app_file)
    app_logger.addHandler(console)
    return app_logger

app_logger = setup_logging()

# --- Config & Constants ---
try:
    with open('config.json', 'r') as config_file:
        config = json.load(config_file)
except FileNotFoundError:
    app_logger.critical("config.json not found. Please create it before running.")
    exit(1)

DEBUG_MODE       = config.get('debug', False)
LOGIN_URL        = config['login_url']
INF_WEBHOOK_URL  = config.get('inf_webhook_url')
TARGET_STORE     = config['target_store']

# --- File & Directory Paths ---
JSON_LOG_FILE   = os.path.join('output', 'inf_items.jsonl')
STORAGE_STATE   = 'state.json'
OUTPUT_DIR      = 'output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Playwright Settings ---
PAGE_TIMEOUT    = 90000
ACTION_TIMEOUT  = 30000
WAIT_TIMEOUT    = 45000
WORKER_RETRY_COUNT = 3

playwright = None
browser = None
log_lock = asyncio.Lock()


# =======================================================================================
#         AUTHENTICATION & SESSION MANAGEMENT (UNCHANGED)
# =======================================================================================

async def _save_screenshot(page: Page | None, prefix: str):
    if not page or page.is_closed(): return
    try:
        path = os.path.join(OUTPUT_DIR, f"{prefix}_{datetime.now(LOCAL_TIMEZONE).strftime('%Y%m%d_%H%M%S')}.png")
        await page.screenshot(path=path, full_page=True, timeout=15000)
        app_logger.info(f"Screenshot saved for debugging: {path}")
    except Exception as e:
        app_logger.error(f"Failed to save screenshot with prefix '{prefix}': {e}")

def ensure_storage_state():
    if not os.path.exists(STORAGE_STATE) or os.path.getsize(STORAGE_STATE) == 0: return False
    try:
        with open(STORAGE_STATE) as f: data = json.load(f)
        return isinstance(data, dict) and "cookies" in data and data["cookies"]
    except json.JSONDecodeError:
        return False

async def perform_login(page: Page) -> bool:
    app_logger.info(f"Navigating to login page: {LOGIN_URL}")
    try:
        await page.goto(LOGIN_URL, timeout=PAGE_TIMEOUT, wait_until="load")
        await page.get_by_label("Email or mobile phone number").fill(config['login_email'])
        await page.get_by_label("Continue").click()
        await page.get_by_label("Password").fill(config['login_password'])
        await page.get_by_label("Sign in").click()
        
        otp_selector = 'input[id*="otp"]'
        dashboard_selector = "#content"
        await page.wait_for_selector(f"{otp_selector}, {dashboard_selector}", timeout=30000)

        if await page.locator(otp_selector).is_visible():
            app_logger.info("OTP is required.")
            otp_code = pyotp.TOTP(config['otp_secret_key']).now()
            await page.locator(otp_selector).fill(otp_code)
            await page.get_by_role("button", name="Sign in").click()

        await page.wait_for_selector(dashboard_selector, timeout=30000)
        app_logger.info("Login process appears successful.")
        return True
    except Exception as e:
        app_logger.critical(f"Critical error during login: {e}", exc_info=DEBUG_MODE)
        await _save_screenshot(page, "login_critical_failure")
        return False

async def prime_master_session() -> bool:
    global browser
    app_logger.info("Priming master session")
    ctx = await browser.new_context()
    try:
        page = await ctx.new_page()
        if not await perform_login(page): return False
        await ctx.storage_state(path=STORAGE_STATE)
        app_logger.info(f"Login successful. Auth state saved to '{STORAGE_STATE}'.")
        return True
    finally:
        await ctx.close()

# =======================================================================================
#                              CORE SCRAPING LOGIC
# =======================================================================================

async def log_inf_results(data: list):
    """Writes INF results to a local JSONL log file."""
    async with log_lock:
        log_entry = {
            'timestamp': datetime.now(LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'),
            'store': TARGET_STORE['store_name'],
            'inf_items': data
        }
        try:
            async with aiofiles.open(JSON_LOG_FILE, 'a', encoding='utf-8') as f:
                await f.write(json.dumps(log_entry) + '\n')
        except IOError as e:
            app_logger.error(f"Error writing to INF JSON log file {JSON_LOG_FILE}: {e}")

async def scrape_inf_data(browser: Browser, store_info: dict, storage_state: dict) -> list[dict] | None:
    """Navigates, sorts, and scrapes the top INF items table."""
    store_name = store_info['store_name']
    app_logger.info(f"Starting INF data collection for '{store_name}'")

    for attempt in range(WORKER_RETRY_COUNT):
        ctx: BrowserContext = None
        page: Page = None
        try:
            ctx = await browser.new_context(storage_state=storage_state)
            page = await ctx.new_page()

            inf_url = (
                f"https://sellercentral.amazon.co.uk/snow-inventory/inventoryinsights/"
                f"?ref_=mp_home_logo_xx&cor=mmp_EU"
                f"&mons_sel_dir_mcid={store_info['merchant_id']}"
                f"&mons_sel_mkid={store_info['marketplace_id']}"
            )
            app_logger.info(f"Navigating to Inventory Insights URL: {inf_url}")
            await page.goto(inf_url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")

            table_selector = "table.imp-table"
            app_logger.info(f"Waiting for INF table container '{table_selector}' to be visible...")
            await expect(page.locator(table_selector)).to_be_visible(timeout=WAIT_TIMEOUT)
            app_logger.info("Table container found.")

            try:
                first_row_locator = page.locator(f"{table_selector} tbody tr").first
                app_logger.info("Waiting for table to be populated with data...")
                await expect(first_row_locator).to_be_visible(timeout=WAIT_TIMEOUT)
                app_logger.info("Table data has loaded.")
            except TimeoutError:
                app_logger.info(f"No data rows appeared for '{store_name}'. Assuming zero INF items.")
                return []

            try:
                sort_header = page.locator("#sort-3")
                app_logger.info("Clicking 'INF Units' header to sort table...")
                await sort_header.click(timeout=ACTION_TIMEOUT)
                await expect(sort_header).to_have_class(re.compile(r'\bimp-sorted\b'), timeout=ACTION_TIMEOUT)
                app_logger.info("Table sorted successfully by INF Units.")
                await page.wait_for_timeout(1000) 
            except Exception as e:
                app_logger.warning(f"Could not sort table by INF Units. Data may not be ordered correctly. Error: {e}")
                await _save_screenshot(page, f"{store_name}_inf_sort_error")

            inf_items = []
            rows = await page.locator(f"{table_selector} tbody tr").all()
            app_logger.info(f"Found {len(rows)} items in the table after sorting.")

            for row in rows:
                try:
                    cells = row.locator("td")
                    thumb_url = await cells.nth(0).locator("img").get_attribute("src")
                    resized_image_url = re.sub(r'\._SS\d+_\.', '._SS250_.', thumb_url) if thumb_url else ""

                    item_data = {
                        'image_url': resized_image_url,
                        'sku': await cells.nth(1).locator("span").inner_text(),
                        'product_name': await cells.nth(2).locator("a span").inner_text(),
                        'inf_units': await cells.nth(3).locator("span").inner_text(),
                        'orders_impacted': await cells.nth(4).locator("span").inner_text(),
                        'inf_pct': await cells.nth(8).locator("span").inner_text(),
                    }
                    inf_items.append(item_data)
                except Exception as e:
                    app_logger.warning(f"Could not parse a row in the INF table. Error: {e}. Skipping row.")
            
            app_logger.info(f"Successfully scraped {len(inf_items)} INF items for {store_name}.")
            return inf_items

        except Exception as e:
            app_logger.warning(f"Attempt {attempt+1} for INF scrape failed for {store_name}: {e}", exc_info=True)
            if attempt == WORKER_RETRY_COUNT - 1:
                app_logger.error(f"All INF scrape attempts failed for {store_name}.")
                await _save_screenshot(page, f"{store_name}_inf_scrape_error")
        finally:
            if ctx: await ctx.close()

    return None

# =======================================================================================
#                                  CHAT WEBHOOK
# =======================================================================================

async def post_inf_to_chat(items: list[dict]):
    """Sends a rich card with the top INF items to a Google Chat webhook."""
    if not INF_WEBHOOK_URL:
        app_logger.warning("INF_WEBHOOK_URL not configured. Skipping chat notification.")
        return
    if not items:
        app_logger.info("post_inf_to_chat called with no items. No report will be sent.")
        return

    store_name = TARGET_STORE.get("store_name", "Unknown Store")
    timestamp = datetime.now(LOCAL_TIMEZONE).strftime("%A %d %B, %H:%M")

    item_widgets = []
    item_widgets.append({"divider": {}})

    for item in items[:12]: 
        encoded_sku = urllib.parse.quote(item['sku'])
        
        # --- KEY CHANGE HERE ---
        # Adjusted the requested QR code size to 100x100
        qr_code_url = f"https://api.qrserver.com/v1/create-qr-code/?size=100x100&data={encoded_sku}"
        
        widget = {
            "columns": {
                "columnItems": [
                    { # Column 1: SKU QR Code
                        "horizontalSizeStyle": "FILL_MINIMUM_SPACE",
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "CENTER",
                        "widgets": [{"image": { 
                            "imageUrl": qr_code_url,
                            "altText": f"QR Code for SKU {item['sku']}"
                        }}]
                    },
                    { # Column 2: Text details and Product Image stacked vertically
                        "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                        "widgets": [
                            {"textParagraph": {
                                "text": (f"<b>{item['product_name']}</b><br>"
                                         f"<b>SKU:</b> {item['sku']}<br>"
                                         f"<b>INF Units:</b> {item['inf_units']} ({item['inf_pct']}) | "
                                         f"<b>Orders:</b> {item['orders_impacted']}")
                            }},
                            {"image": { 
                                "imageUrl": item['image_url'], 
                                "altText": item['product_name']
                            }}
                        ]
                    }
                ]
            }
        }
        item_widgets.append(widget)
        item_widgets.append({"divider": {}})

    payload = {
        "cardsV2": [{
            "cardId": f"inf-report-{store_name.replace(' ', '-')}",
            "card": {
                "header": {
                    "title": f"Top INF Items Report - {store_name}",
                    "subtitle": f"Sorted by INF Units | {timestamp}",
                    "imageUrl": "https://cdn-icons-png.flaticon.com/512/2838/2838885.png",
                    "imageType": "CIRCLE"
                },
                "sections": [{"widgets": item_widgets}]
            }
        }]
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
            async with session.post(INF_WEBHOOK_URL, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    app_logger.error(f"INF chat webhook failed. Status: {resp.status}, Response: {error_text}")
                else:
                    app_logger.info("Successfully posted INF report to chat webhook.")
    except Exception as e:
        app_logger.error(f"Error posting INF report to chat webhook: {e}", exc_info=True)


# =======================================================================================
#                                  MAIN EXECUTION
# =======================================================================================

async def main():
    global playwright, browser
    app_logger.info("Starting up INF Items Scraper...")
    
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=not DEBUG_MODE)
        
        if not ensure_storage_state():
             if not await prime_master_session():
                app_logger.critical("Fatal: Could not establish a login session. Aborting.")
                return
        
        with open(STORAGE_STATE) as f:
            storage_state = json.load(f)
            
        inf_data = await scrape_inf_data(browser, TARGET_STORE, storage_state)
        
        if inf_data is not None:
            if inf_data:
                await log_inf_results(inf_data)
                await post_inf_to_chat(inf_data)
            else:
                app_logger.info("Run completed: No INF items to report today.")
            app_logger.info("INF scraper run completed successfully.")
        else:
            app_logger.error("INF scraper run failed: Could not retrieve data for the target store.")

    except Exception as e:
        app_logger.critical(f"A critical error occurred in main execution: {e}", exc_info=True)
    finally:
        app_logger.info("Shutting down...")
        if browser: await browser.close()
        if playwright: await playwright.stop()
        app_logger.info("Shutdown complete.")

if __name__ == "__main__":
    asyncio.run(main())