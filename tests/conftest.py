import json
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _REPO_ROOT / "config.json"
_CONFIG_CREATED = False

if not _CONFIG_PATH.exists():
    _CONFIG = {
        "login_url": "https://example.com/login",
        "target_store": {
            "store_name": "Test Store",
            "merchant_id": "TEST",
            "marketplace_id": "TEST",
            "morrisons_location_id": "TEST",
        },
        "inf_webhook_url": "",
        "single_card": False,
        "enable_stock_lookup": False,
        "enable_supabase_upload": False,
        "email_report": False,
        "email_settings": {},
    }
    _CONFIG_PATH.write_text(json.dumps(_CONFIG))
    _CONFIG_CREATED = True


def pytest_sessionfinish(session, exitstatus):  # type: ignore[override]
    if _CONFIG_CREATED:
        _CONFIG_PATH.unlink(missing_ok=True)
