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
    LOCAL_TIMEZONE,
    JSON_LOG_FILE,
    EMAIL_REPORT,
    SMTP_SERVER,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_PASSWORD,
    EMAIL_FROM,
    EMAIL_TO,
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


async def post_inf_to_chat(items: list[dict]) -> None:
    if not INF_WEBHOOK:
        app_logger.warning("INF_WEBHOOK_URL not set; skipping chat post.")
        return
    if not items:
        app_logger.info("No items to post; skipping chat post.")
        return

    ts = datetime.now(LOCAL_TIMEZONE).strftime("%A %d %B, %H:%M")
    store = TARGET_STORE["store_name"]

    if SINGLE_CARD:
        batches = [items[:BATCH_SIZE]]
        app_logger.info(
            f"SINGLE_CARD enabled: sending 1 card with up to {BATCH_SIZE} items"
        )
    else:
        batches = [items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
        app_logger.info(
            f"SENDING {len(batches)} batch(es) of up to {BATCH_SIZE} items each"
        )

    for idx, batch in enumerate(batches, start=1):
        widgets = [{"divider": {}}]
        for it in batch:
            code = urllib.parse.quote(it["sku"])
            qr = f"https://api.qrserver.com/v1/create-qr-code/?size={QR_CODE_SIZE}x{QR_CODE_SIZE}&data={code}"
            widgets += [
                {
                    "columns": {
                        "columnItems": [
                            {
                                "horizontalSizeStyle": "FILL_MINIMUM_SPACE",
                                "horizontalAlignment": "CENTER",
                                "verticalAlignment": "CENTER",
                                "widgets": [
                                    {
                                        "image": {
                                            "imageUrl": qr,
                                            "altText": f"QR {it['sku']}",
                                        }
                                    }
                                ],
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
                                            )
                                        }
                                    },
                                    {
                                        "image": {
                                            "imageUrl": it["image_url"],
                                            "altText": it["product_name"],
                                        }
                                    },
                                ],
                            },
                        ]
                    }
                },
                {"divider": {}},
            ]

        total = len(batches)
        subtitle = f"Sorted by INF Units | {ts}"
        if not SINGLE_CARD:
            subtitle += f" | batch {idx}/{total}"

        payload = {
            "cardsV2": [
                {
                    "cardId": f"inf-report-{store.replace(' ', '-')}"
                    + (f"-b{idx}" if not SINGLE_CARD else ""),
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

        app_logger.info(f"Posting batch {idx}/{len(batches)} with {len(batch)} items")
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where())
                ),
            ) as session:
                resp = await session.post(INF_WEBHOOK, json=payload)
                if resp.status == 200:
                    app_logger.info(f"Posted batch {idx}/{len(batches)} successfully")
                else:
                    text = await resp.text()
                    app_logger.error(f"Batch {idx} failed ({resp.status}): {text}")
        except Exception as e:
            app_logger.error(f"Error posting batch {idx}: {e}", exc_info=True)


async def email_inf_report(items: list[dict]) -> None:
    if not EMAIL_REPORT:
        app_logger.info("Email report disabled; skipping email send.")
        return
    if not items:
        app_logger.info("No items to email; skipping email send.")
        return

    ts = datetime.now(LOCAL_TIMEZONE).strftime("%A %d %B, %H:%M")
    store = TARGET_STORE["store_name"]

    table_rows = "".join(
        f"<tr><td>{it['sku']}</td><td>{it['product_name']}</td><td>{it['inf_units']}</td>"
        f"<td>{it['orders_impacted']}</td><td>{it['inf_pct']}</td><td></td><td></td></tr>"
        for it in items
    )

    html = f"""
    <html>
      <body>
        <p>Top INF Items Report - {store} ({ts})</p>
        <table border='1' cellpadding='4' cellspacing='0'>
          <tr>
            <th>SKU</th><th>Product</th><th>INF Units</th>
            <th>Orders Impacted</th><th>INF %</th>
            <th>Action taken</th><th>Actioned</th>
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

    await asyncio.to_thread(_send)
