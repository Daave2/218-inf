"""Utilities for syncing log history from GitHub Actions artifacts."""

from __future__ import annotations

import asyncio
import io
import os
import zipfile
from typing import Optional

import aiohttp

from settings import (
    ENABLE_ARTIFACT_LOG_SYNC,
    GITHUB_ARTIFACT_NAME,
    GITHUB_ARTIFACT_REPOSITORY,
    GITHUB_ARTIFACT_TOKEN,
    JSON_LOG_FILE,
    app_logger,
)


_artifact_lock = asyncio.Lock()
_artifact_checked = False


async def ensure_log_history_from_artifact() -> None:
    """Download the latest log artifact if local history is missing."""

    global _artifact_checked

    if _artifact_checked:
        return

    if not ENABLE_ARTIFACT_LOG_SYNC:
        _artifact_checked = True
        return

    if JSON_LOG_FILE and os.path.exists(JSON_LOG_FILE):
        if os.path.getsize(JSON_LOG_FILE) > 0:
            _artifact_checked = True
            return

    async with _artifact_lock:
        if _artifact_checked:
            return

        try:
            await _download_log_history()
        finally:
            _artifact_checked = True


async def _download_log_history() -> None:
    if not GITHUB_ARTIFACT_NAME:
        app_logger.warning(
            "Artifact log sync enabled but no artifact name configured; skipping."
        )
        return

    if not GITHUB_ARTIFACT_REPOSITORY:
        app_logger.warning(
            "Artifact log sync enabled but repository is unknown; skipping."
        )
        return

    if not GITHUB_ARTIFACT_TOKEN:
        app_logger.warning(
            "Artifact log sync enabled but no GitHub token available; skipping."
        )
        return

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_ARTIFACT_TOKEN}",
        "User-Agent": "inf-artifact-sync",
    }

    artifacts_url = (
        f"https://api.github.com/repos/{GITHUB_ARTIFACT_REPOSITORY}/actions/artifacts"
    )

    async with aiohttp.ClientSession(headers=headers) as session:
        artifact = await _find_latest_artifact(session, artifacts_url)

        if not artifact:
            app_logger.info(
                "No matching artifact named '%s' was found for log sync.",
                GITHUB_ARTIFACT_NAME,
            )
            return

        await _save_artifact_log(session, artifact["archive_download_url"])


async def _find_latest_artifact(
    session: aiohttp.ClientSession, url: str
) -> Optional[dict]:
    page = 1
    per_page = 100

    while True:
        params = {"per_page": per_page, "page": page}
        async with session.get(url, params=params) as response:
            if response.status != 200:
                text = await response.text()
                app_logger.error(
                    "Failed to list artifacts (status %s): %s",
                    response.status,
                    text,
                )
                return None

            payload = await response.json()
            artifacts = payload.get("artifacts", [])

            for artifact in artifacts:
                if artifact.get("name") != GITHUB_ARTIFACT_NAME:
                    continue
                if artifact.get("expired"):
                    continue
                return artifact

            if len(artifacts) < per_page:
                return None

            page += 1


async def _save_artifact_log(session: aiohttp.ClientSession, download_url: str) -> None:
    async with session.get(download_url) as response:
        if response.status != 200:
            text = await response.text()
            app_logger.error(
                "Failed to download artifact archive (status %s): %s",
                response.status,
                text,
            )
            return

        content = await response.read()

    if not content:
        app_logger.warning("Artifact archive was empty; skipping log sync.")
        return

    log_bytes = _extract_log_from_zip(content)
    if log_bytes is None:
        app_logger.warning(
            "Artifact archive did not contain an 'inf_items.jsonl' file."
        )
        return

    os.makedirs(os.path.dirname(JSON_LOG_FILE), exist_ok=True)

    with open(JSON_LOG_FILE, "wb") as destination:
        destination.write(log_bytes)

    app_logger.info("Downloaded log history from artifact '%s'.", GITHUB_ARTIFACT_NAME)


def _extract_log_from_zip(content: bytes) -> Optional[bytes]:
    buffer = io.BytesIO(content)

    with zipfile.ZipFile(buffer) as archive:
        for name in archive.namelist():
            if name.endswith("inf_items.jsonl"):
                with archive.open(name) as member:
                    return member.read()

    return None
