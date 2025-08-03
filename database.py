import asyncio
from datetime import datetime
import re

from settings import (
    app_logger,
    supabase_client,
    LOCAL_TIMEZONE,
    SMALL_IMAGE_SIZE,
)


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
    """
    if not supabase_client:
        app_logger.warning("Supabase client not configured. Skipping database update.")
        return

    investigation_name = (
        f"INF Scrape - {datetime.now(LOCAL_TIMEZONE).strftime('%Y-%m-%d')}"
    )

    try:
        loop = asyncio.get_running_loop()

        def _get_or_create_investigation():
            existing = (
                supabase_client.table("investigations")
                .select("id")
                .eq("name", investigation_name)
                .maybe_single()
                .execute()
            )
            if existing and existing.data:
                return existing.data["id"]
            created = (
                supabase_client.table("investigations")
                .insert({"name": investigation_name})
                .execute()
            )
            if not created.data:
                msg = (
                    created.message if hasattr(created, "message") else "Unknown error"
                )
                raise Exception(f"Failed to create investigation: {msg}")
            return created.data[0]["id"]

        investigation_id = await loop.run_in_executor(
            None, _get_or_create_investigation
        )
        app_logger.info(
            f"Using investigation '{investigation_name}' with ID: {investigation_id}"
        )

        products_to_insert = [
            {
                "investigation_id": investigation_id,
                "sku": item.get("sku"),
                "product_name": item.get("product_name"),
                "image_url": get_larger_image_url(item.get("image_url")),
                "inf_units": clean_numeric_string(item.get("inf_units", "0")),
                "orders_impacted": clean_numeric_string(
                    item.get("orders_impacted", "0")
                ),
                "successful_substitution_percent": item.get("inf_pct", "0%"),
                "status": "pending",
                "stock_on_hand": item.get("stock_on_hand"),
                "stock_unit": item.get("stock_unit"),
                "stock_last_updated": item.get("stock_last_updated"),
                "std_location": item.get("std_location"),
                "promo_location": item.get("promo_location"),
            }
            for item in items
        ]

        if not products_to_insert:
            app_logger.warning("No valid items to insert into database.")
            return

        app_logger.info(
            f"Upserting {len(products_to_insert)} products for investigation {investigation_id}."
        )
        products_response = await loop.run_in_executor(
            None,
            lambda: supabase_client.table("products")
            .upsert(products_to_insert, on_conflict="investigation_id,sku")
            .execute(),
        )

        if not products_response.data:
            error_message = (
                products_response.message
                if hasattr(products_response, "message")
                else "Unknown error during product insert"
            )
            raise Exception(f"Failed to insert products: {error_message}")

        app_logger.info(
            "Products successfully upserted in Supabase for investigation "
            f"ID {investigation_id}."
        )

    except Exception as e:
        app_logger.error(
            f"An error occurred during the Supabase update: {e}", exc_info=True
        )


async def get_investigation_projects(
    investigation_id: int, organization: str | None = None
) -> list[dict]:
    """Return projects for an investigation, optionally filtered by organization."""
    if not supabase_client:
        app_logger.warning("Supabase client not configured. Skipping database query.")
        return []

    loop = asyncio.get_running_loop()

    def _fetch():
        query = (
            supabase_client.table("projects")
            .select("*")
            .eq("investigation_id", investigation_id)
        )
        if organization:
            query = query.eq("organization", organization)
        return query.execute()

    result = await loop.run_in_executor(None, _fetch)
    return result.data or []