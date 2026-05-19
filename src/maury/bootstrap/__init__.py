"""maury bootstrap — first-run init + curator-side host registration.

Two distinct CLI verbs back this module (per ADR-0018 + ADR-0039):
  - `maury init`            : user's first command on a new host.
  - `maury mode bootstrap`  : curator-side registration of a new host
                              against an existing mode in the manifest.

Currently shipped:
  - `maury init`           → this module's `init_cmd.init`
  - `maury mode bootstrap` → this module's `host_cmd.bootstrap_host`
  - `maury mode deregister`→ this module's `deregister.deregister_host`

History: the pre-release CLI alias `maury bootstrap host` was retired
2026-05-19 (no users existed to deprecate from). The engine function
`bootstrap_host` exported below is unchanged.
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
