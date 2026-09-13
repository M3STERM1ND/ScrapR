"""Object storage against real MinIO (`DEC-12`, `REQ-SEC-005`).

Against the real thing, not a mock. The whole argument in `DEC-12 §2` is that
the S3 API is the contract and MinIO in `docker compose` is what makes the
upload path locally exercisable — a mocked `boto3` would prove that the calls
were made and nothing about whether a presigned URL actually works, which is
the only interesting question here.

Skips rather than fails when nothing is listening, for the same reason the
database fixtures do: `docker compose up -d` is a setup step, not a test.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import httpx
import pytest
from botocore.exceptions import BotoCoreError, ClientError

from scrapr_core.config import Settings
from scrapr_core.storage.objects import (
    GET_EXPIRY_SECONDS,
    PUT_EXPIRY_SECONDS,
    ObjectStore,
    storage_key_for,
)


def _settings() -> Settings:
    """Defaults, not the developer's `.env`.

    A machine configured against a real R2 bucket must not have its test run
    quietly write objects into production storage.
    """
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def store() -> Iterator[ObjectStore]:
    created = ObjectStore(_settings())
    try:
        created.stored_size("probe/does-not-exist")
    except (BotoCoreError, ClientError, Exception) as exc:
        pytest.skip(f"no object storage reachable ({exc}); run `docker compose up -d`")
    yield created


@pytest.fixture
def key() -> str:
    return f"uploads/{uuid4()}/{uuid4()}.txt"


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------


def test_the_key_is_derived_from_ids_never_from_the_filename() -> None:
    """`REQ-SEC-005 AC-1`, and the reason is path traversal.

    A key built from user-supplied text is a question that has to be answered
    correctly every time it is asked. A key built from two UUIDs is not asked.
    """
    session_id = UUID("01a09949-83a2-7100-9096-17f194151e31")
    upload_id = UUID("01a09949-8398-70b5-949d-0ae507411f23")

    key = storage_key_for(session_id, upload_id, "../../etc/passwd")

    assert key.startswith(f"uploads/{session_id}/{upload_id}.")
    assert ".." not in key
    assert "passwd" not in key


def test_the_extension_is_sanitised_and_bounded() -> None:
    session_id, upload_id = uuid4(), uuid4()

    assert storage_key_for(session_id, upload_id, "report.PDF").endswith(".pdf")
    assert storage_key_for(session_id, upload_id, "noextension").endswith(".bin")
    assert storage_key_for(session_id, upload_id, "x.a/b\\c").endswith(".abc")
    assert storage_key_for(session_id, upload_id, "x." + "z" * 40).endswith("." + "z" * 8)


# --------------------------------------------------------------------------
# The round trip
# --------------------------------------------------------------------------


def test_a_presigned_put_actually_accepts_the_bytes(
    store: ObjectStore, key: str
) -> None:
    """The one test that proves the upload path exists.

    Everything else in Phase 4 assumes a browser can PUT to this URL without
    the API in the middle (implementation plan §10). If that is not true, no
    amount of correct row-writing helps.
    """
    presigned = store.presign_put(key, "text/plain")

    response = httpx.put(
        presigned.url,
        content=b"Revenue was $1.2bn.",
        headers={"content-type": "text/plain"},
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    assert store.stored_size(key) == 19
    store.delete(key)


def test_the_signed_content_type_is_binding(store: ObjectStore, key: str) -> None:
    """A client that sends a different type gets a signature failure.

    That is what makes `uploads.content_type` meaningful before extraction has
    run: the stored object is the type the URL was signed for, so the row and
    the object cannot disagree about what was uploaded.
    """
    presigned = store.presign_put(key, "text/plain")

    response = httpx.put(
        presigned.url,
        content=b"x",
        headers={"content-type": "application/pdf"},
        timeout=10.0,
    )

    assert response.status_code == 403


def test_a_presigned_get_reads_it_back(store: ObjectStore, key: str) -> None:
    store.presign_put(key, "text/plain")
    httpx.put(
        store.presign_put(key, "text/plain").url,
        content=b"hello",
        headers={"content-type": "text/plain"},
        timeout=10.0,
    )

    response = httpx.get(store.presign_get(key), timeout=10.0)

    assert response.status_code == 200
    assert response.content == b"hello"
    store.delete(key)


def test_the_bucket_is_not_readable_without_a_signature(
    store: ObjectStore, key: str
) -> None:
    """`REQ-SEC-005 AC-1`: not publicly listable or guessable.

    The `minio-init` container sets the bucket private on first start. If that
    ever regressed, every uploaded document would be one URL away from anyone
    who could guess two UUIDs — and this is the test that would say so.
    """
    httpx.put(
        store.presign_put(key, "text/plain").url,
        content=b"secret",
        headers={"content-type": "text/plain"},
        timeout=10.0,
    )

    signed = store.presign_get(key)
    unsigned = signed.split("?")[0]

    response = httpx.get(unsigned, timeout=10.0)
    assert response.status_code in (401, 403), (
        "the bucket is serving objects without a signature; it is public"
    )
    store.delete(key)


def test_reading_a_missing_object_reports_absence_not_failure(
    store: ObjectStore,
) -> None:
    """`None`, not an exception. Completion has to tell "not uploaded yet" apart
    from "storage is broken", and answers the client differently for each."""
    assert store.stored_size(f"uploads/{uuid4()}/never-written.txt") is None


def test_delete_is_idempotent(store: ObjectStore, key: str) -> None:
    """What makes the row-outlives-the-object window safe to retry through."""
    store.delete(key)
    store.delete(key)


def test_read_returns_the_bytes_the_worker_will_extract(
    store: ObjectStore, key: str
) -> None:
    httpx.put(
        store.presign_put(key, "text/plain").url,
        content=b"Revenue was $1.2bn.",
        headers={"content-type": "text/plain"},
        timeout=10.0,
    )

    assert store.read(key) == b"Revenue was $1.2bn."
    store.delete(key)


# --------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------


def test_expiry_is_short_enough_to_matter(store: ObjectStore, key: str) -> None:
    """A leaked URL must stop being a write grant.

    Fifteen minutes is long enough for a 25 MB file on a slow connection and
    short enough that a URL in a browser history is not a standing grant.
    """
    assert PUT_EXPIRY_SECONDS == 900
    assert GET_EXPIRY_SECONDS == 300

    presigned = store.presign_put(key, "text/plain")
    assert f"X-Amz-Expires={PUT_EXPIRY_SECONDS}" in presigned.url
    assert presigned.storage_key == key
