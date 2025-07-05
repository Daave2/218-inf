import asyncio
from datetime import datetime
import re

from settings import app_logger, supabase_client, LOCAL_TIMEZONE, SMALL_IMAGE_SIZE


def clean_numeric_string(value: str) -> int:
    """Removes commas from a string and converts it to an integer."""
    try:
        return int(value.replace(",", ""))
    except (ValueError, AttributeError):
        return 0

def get_larger_image_url(thumb_url: str | None) -> str | None:
    """Converts an Amazon thumbnail URL to a larger image URL."""
    if not thumb_url:
        return None
    # Amazon thumbnail URLs often contain size specifiers like ._SS80_.
    # We replace this with a larger size from settings.
    return re.sub(r"\._SS\d+_\.", f"._SS{SMALL_IMAGE_SIZE}_.", thumb_url)


async def create_investigation_from_scrape(items: list[dict]) -> None:
    """
    Creates a new investigation in Supabase and populates it with scraped items.

    This function mimics the "create investigation" flow from the web app,
    creating a new batch and adding the scraped products to it.
    """
    if not supabase_client:
        app_logger.warning("Supabase client not configured. Skipping database update.")
        return

    investigation_name = (
        f"INF Scrape - {datetime.now(LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M')}"
    )
    app_logger.info(f"Creating new investigation in Supabase: '{investigation_name}'")

    try:
        # The supabase-py client is sync, so we run it in an executor
        # to avoid blocking the asyncio event loop.
        loop = asyncio.get_running_loop()

        # Step 1: Create a new investigation record and get its ID
        investigation_response = await loop.run_in_executor(
            None,
            lambda: supabase_client.table("investigations")
            .insert({"name": investigation_name})
            .execute(),
        )
        
        if not investigation_response.data:
            error_message = (
                investigation_response.message
                if hasattr(investigation_response, "message")
                else "Unknown error"
            )
            raise Exception(f"Failed to create investigation: {error_message}")

        investigation_id = investigation_response.data[0]["id"]
        app_logger.info(f"Successfully created investigation with ID: {investigation_id}")

        # Step 2: Prepare product data for bulk insertion, now including the image URL.
        products_to_insert = [
            {
                "investigation_id": investigation_id,
                "sku": item.get("sku"),
                "product_name": item.get("product_name"),
                # --- NEWLY ADDED FIELD ---
                "image_url": get_larger_image_url(item.get("image_url")),
                "inf_units": clean_numeric_string(item.get("inf_units", "0")),
                "orders_impacted": clean_numeric_string(
                    item.get("orders_impacted", "0")
                ),
                "successful_substitution_percent": item.get("inf_pct", "0%"),
                "status": "pending",  # Default status for new items
            }
            for item in items
        ]

        if not products_to_insert:
            app_logger.warning("No valid items to insert into database.")
            return

        # Step 3: Bulk insert all products
        app_logger.info(f"Inserting {len(products_to_insert)} products into Supabase.")
        products_response = await loop.run_in_executor(
            None,
            lambda: supabase_client.table("products").insert(products_to_insert).execute(),
        )

        if not products_response.data:
            error_message = (
                products_response.message
                if hasattr(products_response, "message")
                else "Unknown error during product insert"
            )
            raise Exception(f"Failed to insert products: {error_message}")

        app_logger.info(
            "Successfully inserted all products into Supabase for investigation "
            f"ID {investigation_id}."
        )

    except Exception as e:
        app_logger.error(f"An error occurred during the Supabase update: {e}", exc_info=True)