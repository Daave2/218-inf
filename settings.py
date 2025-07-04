import os
import json
import logging
from datetime import datetime
from pytz import timezone
from logging.handlers import RotatingFileHandler
import asyncio

# Basic constants
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

# Load config
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
SINGLE_CARD   = config.get('single_card', False)

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
OUTPUT_DIR      = 'output'
os.makedirs(OUTPUT_DIR, exist_ok=True)
JSON_LOG_FILE   = os.path.join(OUTPUT_DIR, 'inf_items.jsonl')
STORAGE_STATE   = 'state.json'

PAGE_TIMEOUT       = 90_000
ACTION_TIMEOUT     = 45_000
WAIT_TIMEOUT       = 45_000

log_lock   = asyncio.Lock()
