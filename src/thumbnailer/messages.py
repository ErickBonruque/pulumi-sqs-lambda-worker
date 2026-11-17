"""Parse and validate the SQS message body."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from thumbnailer.errors import InvalidMessage

_IMAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_URL_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class ThumbnailRequest:
    """One image to process.

    ``image_id`` names the output folder. When the message does not give one,
    it is a hash of the URL, so the same URL always maps to the same folder.
    """

    source_url: str
    image_id: str

    @classmethod
    def from_body(cls, body: str) -> ThumbnailRequest:
        """Build a request from a JSON body: ``{"source_url": ..., "image_id": ...}``."""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise InvalidMessage(f"body is not JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise InvalidMessage("body must be a JSON object")

        url = payload.get("source_url")
        if not isinstance(url, str) or not url or len(url) > _MAX_URL_LENGTH:
            raise InvalidMessage("source_url must be a non-empty string (max 2048 chars)")

        image_id = payload.get("image_id")
        if image_id is None:
            image_id = hashlib.sha256(url.encode()).hexdigest()[:20]
        elif not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
            raise InvalidMessage("image_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        elif ".." in image_id:
            raise InvalidMessage("image_id must not contain '..'")

        return cls(source_url=url, image_id=image_id)

    def to_body(self) -> str:
        """Serialise the request back to a message body."""
        return json.dumps({"source_url": self.source_url, "image_id": self.image_id})
