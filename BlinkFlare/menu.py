# BlinkFlare menu registration. Runs automatically when this folder is on the
# Nuke plugin path (see README).
import nuke

_draw = nuke.menu("Nodes").addMenu("Draw")
_menu = _draw.addMenu("BlinkFlare")
_menu.addCommand("BlinkFlare", "import blinkflare; blinkflare.create()")
_menu.addCommand("Upgrade Selected", "import blinkflare; blinkflare.upgrade_selected()")
_menu.addCommand("Save ToolSet", "import blinkflare; blinkflare.save_toolset()")
_menu.addCommand("Check Install...", "import blinkflare; blinkflare.diagnose()")
_menu.addCommand("Build From Compiled BlinkScript",
                 "import blinkflare; blinkflare.create_from_selected()")
