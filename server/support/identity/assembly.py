"""Identity composition; imported only by the assembly root."""

from datetime import datetime, timezone

from server.support.identity.adapters import security
from server.support.identity.adapters.sqlite import SqliteUserRepository
from server.support.identity.service import IdentityService
from server.support.identity.http_dependencies import IdentityDependencies


def create_identity_dependencies(database_path) -> IdentityDependencies:
    """Wire the sqlite user store, hashing gateway and request dependencies."""
    identity = IdentityService(
        users=SqliteUserRepository(database_path),
        security=security,
        clock=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return IdentityDependencies(identity)
