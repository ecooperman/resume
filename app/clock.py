"""The current time the way every column here stores it: UTC, without a time zone attached.

Replaces `datetime.utcnow()`, which Python deprecates. Same value and same naive type (so it still compares with what is already in the
database), without the warning."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
