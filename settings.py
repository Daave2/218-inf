import os
import json
import logging
from datetime import datetime
from pytz import timezone
from logging.handlers import RotatingFileHandler
import asyncio
from supabase import create_client, Client

# Basic constants
LOCAL_TIMEZONE = timezone("Europe/London")
TABLE_POLL_DELAY = 1.0  # seconds to wait after table actions
SORT_DELAY = 1.0  # wait after clicking a column header to sort
DATE_FILTER_DELAY = 2.0  # extra wait after selecting the date filter
BATCH_SIZE = 30  # max items per webhook message
SMALL_IMAGE_SIZE = 300  # px for product thumbnails used in chat messages
EMAIL_THUMBNAIL_SIZE = 80  # px for product images in email
QR_CODE_SIZE = 60  # px for QR codes


class LocalTimeFormatter(logging.Formatter):
    def converter(self, ts: float):
        return datetime.fromtimestamp(ts, LOCAL_TIMEZONE).timetuple()


def setup_logging():
    log = logging.getLogger("inf_app")
    log.setLevel(logging.INFO)
    fh = RotatingFileHandler("inf_app.log", maxBytes=10**7, backupCount=5)
    fh.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)s %(message)s"))
    ch = logging.StreamHandler()
    ch.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    log.addHandler(ch)
    return log


app_logger = setup_logging()

# Load config
try:
    with open("config.json", "r") as f:
        config = json.load(f)
except FileNotFoundError:
    app_logger.critical("config.json not found. Please create it before running.")
    exit(1)


# Supabase Client
ENABLE_SUPABASE_UPLOAD = config.get("enable_supabase_upload", True)
SUPABASE_URL = config.get("supabase_url")
SUPABASE_KEY = config.get("supabase_service_key")
supabase_client: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    app_logger.info("Supabase client initialized.")
elif ENABLE_SUPABASE_UPLOAD:
    app_logger.warning(
        "Supabase upload is enabled, but credentials were not found. "
        "Database integration will be skipped."
    )


# Stock Checker settings
ENABLE_STOCK_LOOKUP = config.get("enable_stock_lookup", False)
MORRISONS_API_KEY = config.get("morrisons_api_key")
MORRISONS_BEARER_TOKEN = config.get("morrisons_bearer_token")
MORRISONS_LOCATION_ID = config.get("target_store", {}).get("morrisons_location_id")
if ENABLE_STOCK_LOOKUP and not all([MORRISONS_API_KEY, MORRISONS_LOCATION_ID]):
    app_logger.warning(
        "Stock lookup is enabled, but Morrisons API key or location ID is missing."
    )


EMAIL_THUMBNAIL_SIZE = config.get("thumbnail_size", EMAIL_THUMBNAIL_SIZE)

DEBUG_MODE = config.get("debug", False)
LOGIN_URL = config["login_url"]
INF_WEBHOOK = config.get("inf_webhook_url")
TARGET_STORE = config["target_store"]
SINGLE_CARD = config.get("single_card", False)

# Email settings
EMAIL_REPORT = config.get("email_report", False)
EMAIL_SETTINGS = config.get("email_settings", {})
SMTP_SERVER = EMAIL_SETTINGS.get("smtp_server")
SMTP_PORT = EMAIL_SETTINGS.get("smtp_port", 587)
SMTP_USERNAME = EMAIL_SETTINGS.get("smtp_username")
SMTP_PASSWORD = EMAIL_SETTINGS.get("smtp_password")
EMAIL_FROM = EMAIL_SETTINGS.get("from_addr")
EMAIL_TO = EMAIL_SETTINGS.get("to_addr")

# Pre-built URL for navigating directly to the Inventory Insights page for the
# configured store. Using this URL immediately after login bypasses the account
# picker screen when multiple stores are associated with the credentials.
INVENTORY_URL = (
    "https://sellercentral.amazon.co.uk/snow-inventory/inventoryinsights/"
    f"?ref_=mp_home_logo_xx&cor=mmp_EU"
    f"&mons_sel_dir_mcid={TARGET_STORE['merchant_id']}"
    f"&mons_sel_mkid={TARGET_STORE['marketplace_id']}"
)

# Paths & timeouts
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)
JSON_LOG_FILE = os.path.join(OUTPUT_DIR, "inf_items.jsonl")
STORAGE_STATE = "state.json"

PAGE_TIMEOUT = 120_000
ACTION_TIMEOUT = 60_000
WAIT_TIMEOUT = 60_000

LOGIN_RETRIES = 3
SCRAPE_RETRIES = 3

log_lock = asyncio.Lock()