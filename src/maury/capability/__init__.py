"""maury capability — runtime host probe and capability schema.

The probe runs on every `maury sync` and writes
`<repo>/profiles/<profile>/hosts/<host>/capabilities.json`. The render
engine reads this to resolve action-form hooks (`notify`, `log_jsonl`,
...) into platform-specific commands and to skip hooks whose required
capabilities are missing or blocked.

See ADR-0006 for the rationale behind the action-abstraction model.
"""

# Re-export `probe` under a non-conflicting name. If we re-exported as
# `probe`, `maury.capability.probe` would resolve to the FUNCTION rather
# than the SUBMODULE — which then breaks `import maury.capability.probe
# as probe_module` in tests (and is just plain confusing). Naming the
# re-export `run_probe` keeps the submodule name reachable.
from .probe import probe as run_probe
from .schema import (
    OS,
    Capabilities,
    NotificationMech,
    PrivilegedWrites,
    SecurityPosture,
    ToolPresence,
    UserlandFlavor,
    dumps,
    from_dict,
    loads,
    to_dict,
)

__all__ = [
    "OS",
    "Capabilities",
    "NotificationMech",
    "PrivilegedWrites",
    "SecurityPosture",
    "ToolPresence",
    "UserlandFlavor",
    "dumps",
    "from_dict",
    "loads",
    "run_probe",
    "to_dict",
]
