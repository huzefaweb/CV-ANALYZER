"""Pure domain kernel.

Import ban (AR-4, architecture invariant #1): modules under this package may
import stdlib only — no web framework, database driver, filesystem
implementation, identity SDK, parser SDK, provider SDK, or concrete clock.
Enforced by tests/test_domain_boundary.py.
"""
