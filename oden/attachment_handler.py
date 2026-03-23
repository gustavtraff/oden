"""
Attachment handling for Signal messages.

Manages downloading, saving, and linking attachments from messages.
"""

import base64
import datetime
import logging
import os
from typing import Any

from oden.formatting import create_message_filename

logger = logging.getLogger(__name__)


async def _get_attachment_data(attachment_id: str) -> str | None:
    """
    Makes a JSON-RPC call to signal-cli to get attachment data by ID.
    Returns base64 encoded data if successful, otherwise None.
    """
    from oden import config as cfg
    from oden.app_state import get_app_state

    response = await get_app_state().send_jsonrpc(
        "getAttachment",
        params={"account": cfg.SIGNAL_NUMBER, "id": attachment_id},
        timeout=30.0,
    )
    if response and "result" in response:
        return response["result"].get("data")
    logger.error("Failed to get attachment data for ID %s: %s", attachment_id, response)
    return None


async def save_attachments(
    attachments: list[dict[str, Any]],
    group_dir: str,
    dt: datetime.datetime,
    source_name: str | None,
    source_number: str | None,
) -> list[str]:
    """Saves attachments to a subdirectory and returns a list of markdown links."""
    attachment_links = []
    if not attachments:
        return attachment_links

    # Create a unique subdirectory for attachments for this specific message entry
    attachment_subdir_name = (
        dt.strftime("%Y%m%d%H%M%S") + "_" + create_message_filename(dt, source_name, source_number).replace(".md", "")
    )
    attachment_dir = os.path.join(group_dir, attachment_subdir_name)
    try:
        os.makedirs(attachment_dir, exist_ok=True)
    except OSError as e:
        logger.error("Failed to create attachment directory %s: %s", attachment_dir, e)
        return attachment_links

    for i, attachment in enumerate(attachments):
        data = attachment.get("data")
        filename = attachment.get("filename") or attachment.get("id")
        attachment_id = attachment.get("id")

        if not data and attachment_id:
            logger.info(f"Attempting to fetch attachment data for ID: {attachment_id}")
            retrieved_data = await _get_attachment_data(attachment_id)
            if retrieved_data:
                data = retrieved_data
                logger.info(f"Successfully fetched data for attachment ID: {attachment_id}")
            else:
                logger.warning(f"Failed to fetch data for attachment ID: {attachment_id}")

        if data and filename:
            try:
                decoded_data = base64.b64decode(data)
                # Sanitize filename to prevent path traversal attacks
                # os.path.basename strips directory components like "../" or "subdir/"
                sanitized_filename = os.path.basename(filename)
                if not sanitized_filename:
                    # Use attachment ID for uniqueness if available, otherwise use index
                    sanitized_filename = f"attachment_{attachment_id or i + 1}"
                safe_filename = f"{i + 1}_{sanitized_filename}"
                attachment_filepath = os.path.join(attachment_dir, safe_filename)
                with open(attachment_filepath, "wb") as f:
                    f.write(decoded_data)

                attachment_links.append(f"![[{attachment_subdir_name}/{safe_filename}]]")
                logger.info(f"Saved attachment: {attachment_filepath}")
            except Exception as e:
                logger.error(f"Could not save attachment {filename}. Error: {e}")
        else:
            missing_parts = [p for p, v in [("data", data), ("filename", filename)] if not v]
            logger.warning(f"Attachment missing {' and '.join(missing_parts)}: {attachment}")

    return attachment_links
