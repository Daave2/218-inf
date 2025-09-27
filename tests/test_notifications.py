import asyncio
import json
from datetime import datetime, timedelta

import pytest

import notifications


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "inf_items.jsonl"
    monkeypatch.setattr(notifications, "JSON_LOG_FILE", str(path))
    return path


def test_filter_items_posted_today_without_history(log_file):
    items = [{"sku": "SKU-1"}, {"sku": "SKU-2"}]

    filtered = asyncio.run(notifications.filter_items_posted_today(items))

    assert filtered == items


def test_filter_items_posted_today_ignores_previous_days(log_file):
    yesterday = datetime.now(notifications.LOCAL_TIMEZONE) - timedelta(days=1)
    entry = {
        "timestamp": yesterday.strftime("%Y-%m-%d %H:%M:%S"),
        "inf_items": [{"sku": "SKU-1"}],
    }
    log_file.write_text(json.dumps(entry) + "\n")

    items = [{"sku": "SKU-1"}, {"sku": "SKU-2"}]

    filtered = asyncio.run(notifications.filter_items_posted_today(items))

    assert filtered == items


def test_filter_items_posted_today_excludes_current_day_duplicates(log_file):
    today = datetime.now(notifications.LOCAL_TIMEZONE)
    entry = {
        "timestamp": today.strftime("%Y-%m-%d %H:%M:%S"),
        "inf_items": [{"sku": "SKU-1"}],
    }
    log_file.write_text(json.dumps(entry) + "\n")

    items = [{"sku": "SKU-1"}, {"sku": "SKU-2"}, {"sku": "SKU-3"}]

    filtered = asyncio.run(notifications.filter_items_posted_today(items))

    assert [item["sku"] for item in filtered] == ["SKU-2", "SKU-3"]
