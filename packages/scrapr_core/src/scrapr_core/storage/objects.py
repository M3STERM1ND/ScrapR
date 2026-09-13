"""Object storage, over the S3 API (`DEC-12`).

**The application never handles file bytes.** Implementation plan §10 is
explicit: uploads go by presigned PUT direct to storage, and the API signs and
records rather than proxying. That is a security property as much as a
performance one — bytes the API never touches are bytes it cannot leak, log, or
buffer into memory on a serverless function with a fixed limit.

**One client, two environments.** `DEC-12` makes the S3 API the contract, so
MinIO in `docker compose` and R2 in production are the same code with a
different endpoint. Switching provider is configuration; switching *surface*
would be a rewrite of this module and the export path, which is why the surface
is the part the decision defends.

**Nothing here is public.** `REQ-SEC-005 AC-1` requires storage not be
publicly listable or guessable, and `AC-2` requires access be tied to research
ownership — so every read is a short-lived presigned GET issued to a caller the
API has already authorised, and the bucket itself is private.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final
from uuid import UUID

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from scrapr_core.config import Settings

__all__ = [
    "GET_EXPIRY_SECONDS",
    "PUT_EXPIRY_SECONDS",
    "ObjectStore",
    "StorageError",
    "storage_key_for",
]

PUT_EXPIRY_SECONDS: Final = 900
"""Fifteen minutes to complete an upload.

Long enough for a 25 MB file on a slow connection (`DEC-13`), short enough that
a URL leaked from a browser history is not a standing write grant.
"""

GET_EXPIRY_SECONDS: Final = 300
"""Five minutes to start a download.

`REQ-EXP-008` requires artifacts be served only to the owner via short-lived
signed URLs. Five minutes is a click, not a link worth sharing.
"""


class StorageError(RuntimeError):
    """Storage could not be reached or refused the operation.

    Raised rather than returned: unlike a tool failure, there is no degraded
    research to continue with. An upload that cannot be stored has not
    happened, and saying so is the only honest outcome.
    """


def storage_key_for(session_id: UUID, upload_id: UUID, filename: str) -> str:
    """Where one upload lives.

    Keyed by session and upload id, never by filename. Two reasons, and the
    second is the important one: filenames collide, and a key derived from
    user-supplied text is a path-traversal question nobody should have to keep
    answering. The original name is kept in `uploads.filename`, where it is
    data rather than a path.

    `REQ-SEC-005 AC-1` also asks that locations not be guessable, and a UUIDv7
    pair is not.
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    safe = "".join(character for character in suffix if character.isalnum())[:8]
    return f"uploads/{session_id}/{upload_id}.{safe or 'bin'}"


@final
@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """A URL the browser may PUT to, and when it stops working."""

    url: str
    storage_key: str
    expires_at: dt.datetime


@final
class ObjectStore:
    """Presign, verify and delete. Never read or write bytes."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.storage_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint_url or None,
            aws_access_key_id=settings.storage_access_key or None,
            aws_secret_access_key=settings.storage_secret_key or None,
            region_name=settings.storage_region,
            # SigV4 explicitly: R2 requires it, and MinIO accepts it, so one
            # signature version serves both rather than working locally and
            # failing on deploy.
            config=Config(signature_version="s3v4"),
        )

    def presign_put(
        self, storage_key: str, content_type: str, *, now: dt.datetime | None = None
    ) -> PresignedUpload:
        """A URL the client may upload one object to.

        The content type is signed in. A client that presents a different one
        gets a signature mismatch rather than a stored file whose recorded type
        is a lie — which matters because `DEC-14` determines type from content
        and the recorded value has to match what is actually there.
        """
        try:
            url = self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": storage_key,
                    "ContentType": content_type,
                },
                ExpiresIn=PUT_EXPIRY_SECONDS,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"could not presign an upload: {exc}") from exc

        return PresignedUpload(
            url=str(url),
            storage_key=storage_key,
            expires_at=(now or dt.datetime.now(dt.UTC))
            + dt.timedelta(seconds=PUT_EXPIRY_SECONDS),
        )

    def presign_get(self, storage_key: str) -> str:
        """A short-lived URL to read one object (`REQ-EXP-008`)."""
        try:
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": storage_key},
                    ExpiresIn=GET_EXPIRY_SECONDS,
                )
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"could not presign a download: {exc}") from exc

    def stored_size(self, storage_key: str) -> int | None:
        """How large the stored object actually is, or `None` if absent.

        This is the second half of `REQ-DOC-010 AC-1`. Limits are checked at
        presign against a size the *client* declared, and a presigned URL is
        not a limit — so completion re-checks against the object that actually
        arrived. A client that understated its file gets caught here.
        """
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=storage_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return None
            raise StorageError(f"could not stat the upload: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"could not stat the upload: {exc}") from exc

        size = head.get("ContentLength")
        return int(size) if size is not None else None

    def read(self, storage_key: str) -> bytes:
        """The object's bytes, for extraction.

        The one place the application reads content, and it runs in the worker
        rather than the API — extraction needs the bytes and nothing about the
        request path should. `DEC-13` caps a file at 25 MB, which is what makes
        reading it whole acceptable.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
            body = response["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"could not read the upload: {exc}") from exc
        return bytes(body)

    def delete(self, storage_key: str) -> None:
        """Remove the object (`REQ-SEC-008 AC-2`).

        Idempotent: S3 delete succeeds on a key that is already gone, which is
        what makes the row-outlives-the-object window `uploads.deleted_at`
        documents safe to retry through.
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=storage_key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"could not delete the upload: {exc}") from exc
