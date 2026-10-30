import hashlib
import json

from models import ActionIntent, AuthorizationGrant
from datetime import datetime, timezone

def hash_action_intent(
    intent: ActionIntent,
) -> str:
    """Create a stable fingerprint of the proposed action."""

    payload = json.dumps(
        intent.model_dump(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def validate_authorization(
    intent: ActionIntent,
    grant: AuthorizationGrant,
) -> bool:
    """Confirm the approval matches, is active, and has not been used."""

    current_action_hash = hash_action_intent(intent)

    if current_action_hash != grant.action_hash:
        return False

    if datetime.now(timezone.utc) >= grant.expires_at:
        return False

    if grant.used:
        return False

    return True

def consume_grant(
    grant: AuthorizationGrant,
) -> AuthorizationGrant:
    """Return a new version of the grant marked as used."""

    return grant.model_copy(
        update={
            "used": True,
        }
    )

def create_idempotency_key(
    intent: ActionIntent,
) -> str:
    """Create a stable key for one intended business action."""

    return hash_action_intent(intent)