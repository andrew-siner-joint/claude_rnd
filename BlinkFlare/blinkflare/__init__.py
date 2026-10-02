"""BlinkFlare: GPU-accelerated procedural lens flares for Nuke.

    import blinkflare
    blinkflare.create()

Nuke-dependent modules are imported lazily so the spec and presets can be
used (and tested) outside Nuke.
"""

from blinkflare.spec import VERSION  # noqa: F401


def create(on_done=None, on_error=None):
    """Create a BlinkFlare node connected to the selected node.

    The first time, the kernel compiles in the background and the node
    appears when it's ready (``on_done(node)``); after that it's instant.
    """
    from blinkflare import builder
    return builder.create(on_done, on_error)


def create_from_selected():
    """Build BlinkFlare around a selected, hand-compiled BlinkScript node."""
    import nuke
    from blinkflare import builder
    try:
        node = nuke.selectedNode()
    except ValueError:
        node = None
    try:
        return builder.create_from_kernel(node)
    except builder.BuildError as e:
        nuke.message(str(e))


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


def diagnose():
    """Run install_blinkflare.py: checks the install, writes a report."""
    import os
    import runpy
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runpy.run_path(os.path.join(root, "install_blinkflare.py"), run_name="__main__")


def save_toolset(path=None, on_done=None):
    """Save a ready-built BlinkFlare as a ToolSet .nk (for non-NukeX seats)."""
    from blinkflare import builder
    return builder.save_toolset(path, on_done)
