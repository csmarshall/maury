"""maury bootstrap — first-run init + curator-side repo/profile creation.

Per ADR-0018, two distinct verbs:
  - `maury init`         : user's first command on a new host
  - `maury bootstrap *`  : curator-side ops (create repo, register host)

Currently shipped:
  - `maury init` (this module's `init_cmd.init`)
"""

from .init_cmd import InitError, InitResult, init

__all__ = ["InitError", "InitResult", "init"]
