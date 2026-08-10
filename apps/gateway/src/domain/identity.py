"""Pure normalized identity shape and adapter port (AR-5/AR-6/AD-21).

Every identity adapter — local or Auth0 — normalizes to this same
(issuer, immutable subject) pair. No web framework, DB driver, or SDK
import belongs here; this is the shape descendant authorization checks
depend on, not how it was produced.

IDENTITY_PORT_FUNCTIONS documents the module-level function contract every
adapter module (`local_identity`, `auth0_identity`) must expose with matching
names/arity, enforced by tests/test_identity_port.py. A class-based Protocol
was not used: adapters are modules (matching the codebase's existing
module-as-namespace style, e.g. `config.load_settings`), not classes, so a
plain documented + tested contract is the smaller correct fit.
"""

from __future__ import annotations

from dataclasses import dataclass

LOCAL_ISSUER = "local"

IDENTITY_PORT_FUNCTIONS = ("register", "authenticate", "validate_session", "revoke_session")


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
