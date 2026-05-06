"""maury render — assemble base + profile chain + host overlay into ~/.claude/.

See ADR-0019 for the inheritance / refinement semantics this module
implements. Public API re-exported here.
"""

from .engine import (
    LayerSource,
    RenderedFile,
    RenderError,
    RenderResult,
    apply_render,
    render,
)

__all__ = [
    "LayerSource",
    "RenderError",
    "RenderResult",
    "RenderedFile",
    "apply_render",
    "render",
]
