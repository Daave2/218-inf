import json
import ssl
import urllib.parse
from datetime import datetime
import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiohttp
import aiofiles
import certifi

from settings import (
    INF_WEBHOOK,
    TARGET_STORE,
    SINGLE_CARD,
    BATCH_SIZE,
    QR_CODE_SIZE,
    SMALL_IMAGE_SIZE,
    EMAIL_THUMBNAIL_SIZE,
    LOCAL_TIMEZONE,
    JSON_LOG_FILE,
    EMAIL_REPORT,
    SMTP_SERVER,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_PASSWORD,
    EMAIL_FROM,
    EMAIL_TO,
    ENABLE_STOCK_LOOKUP,
    app_logger,
    log_lock,
)


async def log_inf_results(data: list) -> None:
    async with log_lock:
        entry = {
            "timestamp": datetime.now(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
            "store": TARGET_STORE["store_name"],
            "inf_items": data,
        }
        try:
            async with aiofiles.open(JSON_LOG_FILE, "a", encoding="utf-8") as f:
                await f.write(json.dumps(entry) + "\n")
            app_logger.info("Logged INF results to file.")
        except Exception as e:
            app_logger.error(f"Log write error: {e}")


async def filter_items_posted_today(items: list[dict]) -> list[dict]:
    """Remove items that were already logged earlier in the same day."""

    if not items:
        return []

    today = datetime.now(LOCAL_TIMEZONE).date()
    posted_skus: set[str] = set()

    async with log_lock:
        try:
            async with aiofiles.open(JSON_LOG_FILE, "r", encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        app_logger.warning(
                            "Skipping malformed log entry while checking duplicates."
                        )
                        continue

                    timestamp = entry.get("timestamp")
                    if not timestamp:
                        continue
                    try:
                        logged_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        app_logger.warning(
                            "Unexpected timestamp format in log entry: %s", timestamp
                        )
                        continue

                    if logged_dt.date() != today:
                        continue

                    for logged_item in entry.get("inf_items", []):
                        sku = logged_item.get("sku")
                        if sku:
                            posted_skus.add(str(sku))
        except FileNotFoundError:
            return items
        except Exception as exc:
            app_logger.error(
                f"Failed to read log history for duplicate filtering: {exc}",
                exc_info=True,
            )
            return items

    if not posted_skus:
        return items

    filtered_items = [item for item in items if str(item.get("sku")) not in posted_skus]
    removed_count = len(items) - len(filtered_items)

    if removed_count:
        app_logger.info(
            "Filtered %s previously posted INF item(s) for today.", removed_count
        )

    return filtered_items


async def post_inf_to_chat(items: list[dict]) -> None:
    if not INF_WEBHOOK:
        app_logger.warning("INF_WEBHOOK_URL not set; skipping chat post.")
        return
    if not items:
        app_logger.info("No items to post; skipping chat post.")
        return

    def _aisle_sort_key(it: dict) -> int:
        try:
            return int(it.get("aisle_number"))
        except (TypeError, ValueError):
            return float("inf")

    items = sorted(items, key=_aisle_sort_key)

    ts = datetime.now(LOCAL_TIMEZONE).strftime("%A %d %B, %H:%M")
    store = TARGET_STORE["store_name"]

    if ENABLE_STOCK_LOOKUP:
        zero_stock = [it for it in items if it.get("stock_on_hand") == 0]
        extra_locs = [
            it for it in items if it not in zero_stock and it.get("promo_location")
        ]
        others = [it for it in items if it not in zero_stock and it not in extra_locs]
        categories = [
            ("Stock with 0 stock record", zero_stock),
            ("Items with additional locations", extra_locs),
            ("Everything else", others),
        ]
    else:
        categories = [("INF Items", items)]

    for cat_label, cat_items in categories:
        if not cat_items:
            continue

        cat_items.sort(key=_aisle_sort_key)

        if SINGLE_CARD:
            batches = [cat_items[:BATCH_SIZE]]
        else:
            batches = [
                cat_items[i : i + BATCH_SIZE]
                for i in range(0, len(cat_items), BATCH_SIZE)
            ]

        app_logger.info(
            f"Sending {len(batches)} batch(es) for '{cat_label}' with up to {BATCH_SIZE} items each."
        )

        cat_slug = cat_label.lower().replace(" ", "-")
        for idx, batch in enumerate(batches, start=1):
            widgets = [{"divider": {}}]
            for it in batch:
                code = urllib.parse.quote(it["sku"])
                qr = (
                    "https://api.qrserver.com/v1/create-qr-code/?size="
                    f"{QR_CODE_SIZE}x{QR_CODE_SIZE}&data={code}"
                )

                extra_info = ""
                if ENABLE_STOCK_LOOKUP:
                    stock_on_hand = it.get("stock_on_hand")
                    if stock_on_hand is not None:
                        extra_info += f"<br><b>Stock Record:</b> {stock_on_hand}"
                    else:
                        extra_info += "<br><b>Stock Record:</b> Not Found"

                    if it.get("std_location"):
                        extra_info += f"<br><b>Std Loc:</b> {it['std_location']}"
                    if it.get("promo_location"):
                        extra_info += f"<br><b>Promo Loc:</b> {it['promo_location']}"

                widgets += [
                    {
                        "columns": {
                            "columnItems": [
                                {
                                    "horizontalSizeStyle": "FILL_MINIMUM_SPACE",
                                    "horizontalAlignment": "CENTER",
                                    "verticalAlignment": "CENTER",
                                    "widgets": [{"image": {"imageUrl": qr}}],
                                },
                                {
                                    "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                                    "widgets": [
                                        {
                                            "textParagraph": {
                                                "text": (
                                                    f"<b>{it['product_name']}</b><br>"
                                                    f"<b>SKU:</b> {it['sku']}<br>"
                                                    f"<b>INF Units:</b> {it['inf_units']} ({it['inf_pct']}) | "
                                                    f"<b>Orders:</b> {it['orders_impacted']}"
                                                    f"{extra_info}"
                                                )
                                            }
                                        },
                                        {"image": {"imageUrl": it["image_url"]}},
                                    ],
                                },
                            ]
                        }
                    },
                    {"divider": {}},
                ]

            subtitle = f"{cat_label} | {ts}"
            if not SINGLE_CARD:
                subtitle += f" | batch {idx}/{len(batches)}"

            payload = {
                "cardsV2": [
                    {
                        "cardId": f"inf-report-{store.replace(' ', '-')}-{cat_slug}-{idx}",
                        "card": {
                            "header": {
                                "title": f"Top INF Items Report - {store}",
                                "subtitle": subtitle,
                                "imageUrl": "https://cdn-icons-png.flaticon.com/512/2838/2838885.png",
                                "imageType": "CIRCLE",
                            },
                            "sections": [{"widgets": widgets}],
                        },
                    }
                ]
            }
            app_logger.info(
                f"Posting '{cat_label}' batch {idx}/{len(batches)} with {len(batch)} items"
            )
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                    connector=aiohttp.TCPConnector(
                        ssl=ssl.create_default_context(cafile=certifi.where())
                    ),
                ) as session:
                    resp = await session.post(INF_WEBHOOK, json=payload)
                    if resp.status == 200:
                        app_logger.info(
                            f"Posted '{cat_label}' batch {idx}/{len(batches)} successfully"
                        )
                    else:
                        text = await resp.text()
                        app_logger.error(
                            f"{cat_label} batch {idx} failed ({resp.status}): {text}"
                        )
            except Exception as e:
                app_logger.error(
                    f"Error posting '{cat_label}' batch {idx}: {e}", exc_info=True
                )


async def email_inf_report(items: list[dict]) -> None:
    if not EMAIL_REPORT:
        return
    if not items:
        app_logger.info("No items to email; skipping email send.")
        return

    ts = datetime.now(LOCAL_TIMEZONE).strftime("%A %d %B, %H:%M")
    store = TARGET_STORE["store_name"]

    table_rows = "".join(
        f"<tr>"
        f"<td><img src=\"{it['image_url']}\" width=\"{EMAIL_THUMBNAIL_SIZE}\"></td>"
        f"<td>{it['sku']}</td>"
        f"<td>{it['product_name']}</td>"
        f"<td>{it['inf_units']}</td>"
        f"<td>{it['orders_impacted']}</td>"
        f"<td>{it['inf_pct']}</td>"
        f"<td>{it.get('stock_on_hand', '')}</td>"
        f"<td>{it.get('std_location', '')}</td>"
        f"<td>{it.get('promo_location', '')}</td>"
        f"<td><img src=\"https://api.qrserver.com/v1/create-qr-code/?size={QR_CODE_SIZE}x{QR_CODE_SIZE}&data={urllib.parse.quote(it['sku'])}\"></td>"
        f"<td></td><td></td>"
        f"</tr>"
        for it in items
    )

    html = f"""
    <html>
      <head>
        <style>
          table {{ border-collapse: collapse; width: 100%; font-family: sans-serif; font-size: 12px; }}
          th, td {{ border: 1px solid #dddddd; text-align: left; padding: 8px; vertical-align: middle; }}
          tr:nth-child(even) {{ background-color: #f2f2f2; }}
          img {{ display: block; }}
        </style>
      </head>
      <body>
        <p>Top INF Items Report - {store} ({ts})</p>
        <table border='1' cellpadding='4' cellspacing='0'>
          <tr>
            <th>Image</th><th>SKU</th><th>Product</th><th>INF Units</th>
            <th>Orders</th><th>INF %</th><th>Stock</th>
            <th>Std Loc</th><th>Promo Loc</th><th>QR Code</th>
            <th>Action Taken</th><th>Actioned</th>
          </tr>
          {table_rows}
        </table>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"INF Report - {store}"
    msg["From"] = EMAIL_FROM or ""
    msg["To"] = EMAIL_TO or ""
    msg.attach(MIMEText(html, "html"))

    def _send():
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls()
            if SMTP_USERNAME:
                s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.sendmail(msg["From"], [msg["To"]], msg.as_string())

    try:
        await asyncio.to_thread(_send)
        app_logger.info(f"Email report sent to {EMAIL_TO}")
    except Exception as e:
        app_logger.error(f"Failed to send email report: {e}", exc_info=True)
