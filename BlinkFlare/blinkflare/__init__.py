"""BlinkFlare: GPU-accelerated procedural lens flares for Nuke.

    import blinkflare
    blinkflare.create()

Nuke-dependent modules are imported lazily so the spec and presets can be
used (and tested) outside Nuke.
"""

from blinkflare.spec import VERSION  # noqa: F401


def create():
    """Create a BlinkFlare node connected to the selected node."""
    from blinkflare import builder
    return builder.create()


def apply_preset(node, name=None):
    """Apply a preset (default: the one picked in the node's Preset menu)."""
    from blinkflare import builder
    builder.apply_preset(node, name)


def save_toolset(path=None):
    """Save a ready-built BlinkFlare as a ToolSet .nk (for non-NukeX seats)."""
    from blinkflare import builder
    return builder.save_toolset(path)
