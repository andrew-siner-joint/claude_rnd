# HDRIStitch menu. Runs automatically when this folder is on the Nuke plugin
# path (install.sh adds it; see docs/05-cleanup-nuke.md).
import nuke

_menu = nuke.menu("Nodes").addMenu("HDRIStitch")
_menu.addCommand("Cleanup Script from EXR...", "import hdristitch_nuke; hdristitch_nuke.cleanup_script()")
_menu.addCommand("Nadir Patch", "import hdristitch_nuke; hdristitch_nuke.pole_patch('nadir')")
_menu.addCommand("Zenith Patch", "import hdristitch_nuke; hdristitch_nuke.pole_patch('zenith')")
_menu.addCommand("Orient / Level", "import hdristitch_nuke; hdristitch_nuke.orient()")
_menu.addCommand("Gray Card Calibrate", "import hdristitch_nuke; hdristitch_nuke.gray_card()")
_menu.addCommand("Write HDRI (EXR)", "import hdristitch_nuke; hdristitch_nuke.write()")
