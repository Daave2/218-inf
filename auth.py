import os
import json
import re
import pyotp
from playwright.async_api import Browser, Page, TimeoutError, expect
from datetime import datetime
from settings import (
    OUTPUT_DIR,
    STORAGE_STATE,
    LOCAL_TIMEZONE,
    LOGIN_URL,
    INVENTORY_URL,
    TARGET_STORE,
    PAGE_TIMEOUT,
    WAIT_TIMEOUT,
    ACTION_TIMEOUT,
    app_logger,
    config,
)

async def save_screenshot(page: Page | None, prefix: str) -> None:
    if not page or page.is_closed():
        return
    try:
        path = os.path.join(
            OUTPUT_DIR,
            f"{prefix}_{datetime.now(LOCAL_TIMEZONE).strftime('%Y%m%d_%H%M%S')}.png",
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
    except Exception:
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
        dash_sel   = "#content"
        range_sel  = "#range-selector"
        acct_sel   = 'h1:has-text("Select an account")'
        await page.wait_for_selector(f"{otp_sel}, {dash_sel}, {acct_sel}", timeout=WAIT_TIMEOUT)
        if await page.locator(otp_sel).is_visible():
            code = pyotp.TOTP(config['otp_secret_key']).now()
            await page.locator(otp_sel).fill(code)
            await page.get_by_role("button", name="Sign in").click()
            await page.wait_for_selector(f"{dash_sel}, {acct_sel}", timeout=WAIT_TIMEOUT)
        if await page.locator(acct_sel).is_visible():
            app_logger.warning(
                "Account-picker shown; navigating directly to Inventory Insights to bypass"
            )
            try:
                await page.goto(INVENTORY_URL, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
                await expect(page.locator(range_sel)).to_be_visible(timeout=WAIT_TIMEOUT)
                dash_sel = range_sel
            except Exception as e:
                app_logger.error(f"Failed to bypass account picker: {e}")
                await save_screenshot(page, "login_account_picker")
                return False

        await expect(page.locator(dash_sel)).to_be_visible(timeout=WAIT_TIMEOUT)
        app_logger.info("Login successful.")
        return True

    except Exception as e:
        app_logger.critical(f"Login failed: {e}", exc_info=True)
        await save_screenshot(page, "login_failure")
        return False

async def prime_master_session(browser: Browser) -> bool:
    app_logger.info("Priming master session")
    ctx = await browser.new_context(ignore_https_errors=True)
    try:
        page = await ctx.new_page()
        if not await perform_login(page):
            return False
        try:
            app_logger.info("Visiting Inventory Insights to finalize session")
            await page.goto(INVENTORY_URL, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
            await expect(page.locator("#range-selector")).to_be_visible(timeout=WAIT_TIMEOUT)
        except Exception as e:
            app_logger.warning(f"Inventory Insights navigation failed: {e}")
        await ctx.storage_state(path=STORAGE_STATE)
        app_logger.info("Saved new session state.")
        return True
    finally:
        await ctx.close()
