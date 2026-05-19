"""maury bootstrap — first-run init + curator-side repo/profile creation.

Per ADR-0018, two distinct verbs:
  - `maury init`         : user's first command on a new host
  - `maury bootstrap *`  : curator-side ops (create repo, register host)

Currently shipped:
  - `maury init` (this module's `init_cmd.init`)
  - `maury bootstrap host` (this module's `host_cmd.bootstrap_host`)
"""

from .deregister import DeregisterError, DeregisterResult, deregister_host
from .host_cmd import BootstrapHostError, BootstrapHostResult, bootstrap_host
from .init_cmd import InitError, InitResult, init

__all__ = [
    "BootstrapHostError",
    "BootstrapHostResult",
    "DeregisterError",
    "DeregisterResult",
    "InitError",
    "InitResult",
    "bootstrap_host",
    "deregister_host",
    "init",
]
