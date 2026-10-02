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


def save_preset(node, name=None):
    """Save the node's current look as a preset file; prompts for a name."""
    from blinkflare import builder
    return builder.save_preset(node, name)


def refresh_presets(node):
    """Reload the Preset menu from the built-in and saved presets."""
    from blinkflare import builder
    builder.refresh_presets(node)


def bake_to_2d(node, frames=None):
    """Bake the 3D-projected light into 2D keyframes; prompts for a range."""
    from blinkflare import builder
    builder.bake_to_2d(node, frames)


def build_element_layers(node):
    """Build the per-element kernels used by Output > Element Layers."""
    from blinkflare import builder
    builder.build_element_layers(node)


def save_toolset(path=None):
    """Save a ready-built BlinkFlare as a ToolSet .nk (for non-NukeX seats)."""
    from blinkflare import builder
    return builder.save_toolset(path)
