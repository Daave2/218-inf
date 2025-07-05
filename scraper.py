import asyncio
import re
from typing import Awaitable, Callable
from playwright.async_api import Browser, Page, TimeoutError, expect
from settings import (
    PAGE_TIMEOUT,
    WAIT_TIMEOUT,
    TABLE_POLL_DELAY,
    SMALL_IMAGE_SIZE,
    app_logger,
)


async def wait_for_table_change(
    page: Page, table_sel: str, action: Callable[[], Awaitable]
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
        timeout=WAIT_TIMEOUT,
    )


async def scrape_inf_data(
    browser: Browser,
    store_info: dict,
    storage_state: dict,
    fetch_yesterday: bool = False,
) -> list[dict] | None:
    store = store_info["store_name"]
    app_logger.info(f"Opening context for '{store}'")
    ctx = await browser.new_context(
        storage_state=storage_state, ignore_https_errors=True
    )
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
        await expect(page.locator("#range-selector")).to_be_visible(
            timeout=WAIT_TIMEOUT
        )
        app_logger.info("Date-picker is visible.")

        table_sel = "table.imp-table tbody"
        if fetch_yesterday:
            app_logger.info("Applying 'Yesterday' filter")
            link = page.get_by_role("link", name="Yesterday")
            await wait_for_table_change(page, table_sel, lambda: link.click())

        try:
            await expect(page.locator(f"{table_sel} tr").first).to_be_visible(
                timeout=20000
            )
        except TimeoutError:
            app_logger.info("No data rows found; exiting scrape cleanly.")
            return []

        app_logger.info("Setting pageSize to 250 via <select>")
        try:
            await wait_for_table_change(
                page,
                table_sel,
                lambda: page.select_option('select[name="pageSizeDropDown"]', "250"),
            )
        except TimeoutError:
            app_logger.warning(
                "Timed out waiting for pageSize change—"
                "assuming table has already loaded at 250 rows."
            )

        app_logger.info("Sorting table by 'INF Units'")
        await wait_for_table_change(
            page, table_sel, lambda: page.locator("#sort-3").click()
        )

        rows = await page.locator(f"{table_sel} tr").all()
        app_logger.info(f"Found {len(rows)} rows; extracting data")

        items = []
        for r in rows:
            try:
                cells = r.locator("td")
                thumb = await cells.nth(0).locator("img").get_attribute("src") or ""
                img = re.sub(r"\._SS\d+_\.", f"._SS{SMALL_IMAGE_SIZE}_.", thumb)
                items.append(
                    {
                        "image_url": img,
                        "sku": await cells.nth(1).locator("span").inner_text(),
                        "product_name": await cells.nth(2)
                        .locator("a span")
                        .inner_text(),
                        "inf_units": await cells.nth(3).locator("span").inner_text(),
                        "orders_impacted": await cells.nth(4)
                        .locator("span")
                        .inner_text(),
                        "inf_pct": await cells.nth(8).locator("span").inner_text(),
                    }
                )
            except Exception as e:
                app_logger.warning(f"Failed to parse row: {e}")

        app_logger.info(f"Scraped {len(items)} INF items for '{store}'")
        return items

    except Exception as e:
        app_logger.error(f"Error during scrape: {e}", exc_info=True)
        return None

    finally:
        await ctx.close()
