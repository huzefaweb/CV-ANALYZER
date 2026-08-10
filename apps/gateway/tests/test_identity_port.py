"""AD-21: local and Auth0 adapter modules must expose the same function
contract (IDENTITY_PORT_FUNCTIONS) so switching the active adapter needs no
downstream rework. See src/domain/identity.py for why this is a documented
module-function contract rather than a class-based Protocol.
"""

from __future__ import annotations

import inspect

from src.adapters import auth0_identity, local_identity
from src.domain.identity import IDENTITY_PORT_FUNCTIONS


def test_both_adapters_expose_the_same_port_functions():
    for name in IDENTITY_PORT_FUNCTIONS:
        assert hasattr(local_identity, name), f"local_identity missing '{name}'"
        assert hasattr(auth0_identity, name), f"auth0_identity missing '{name}'"


def test_both_adapters_agree_on_function_arity():
    for name in IDENTITY_PORT_FUNCTIONS:
        local_params = list(inspect.signature(getattr(local_identity, name)).parameters)
        auth0_params = list(inspect.signature(getattr(auth0_identity, name)).parameters)
        assert local_params == auth0_params, (
            f"'{name}' signature mismatch: local={local_params} auth0={auth0_params}"
        )
