"""Finding and running Hugin's command-line tools."""
import glob
import os
import shutil
import subprocess
from pathlib import Path

TOOLS = ["pto_gen", "pto_var", "cpfind", "cpclean", "autooptimiser", "pano_modify", "nona",
         "checkpto", "pano_trafo", "linefind"]


class HuginError(RuntimeError):
    pass


def _bundle_dirs():
    """Where Hugin's tools live on a Mac: the official download (a "Hugin"
    folder in /Applications), MacPorts (hugin-app), or a Homebrew tap."""
    home = os.path.expanduser("~")
    pats = ["/Applications/Hugin*/Hugin.app/Contents/MacOS",
            "/Applications/Hugin.app/Contents/MacOS",
            "/Applications/MacPorts/Hugin*.app/Contents/MacOS",
            home + "/Applications/Hugin*/Hugin.app/Contents/MacOS",
            home + "/Applications/Hugin.app/Contents/MacOS",
            "/opt/local/bin", "/opt/homebrew/bin", "/usr/local/bin"]
    found = []
    for pat in pats:
        found.extend(sorted(glob.glob(pat), reverse=True))
    # some bundles keep the tools deeper inside; look once, by name
    for root in ("/Applications/Hugin*", "/Applications/MacPorts/Hugin*",
                 "/Applications/Hugin*.app"):
        for hit in glob.glob(root + "/**/nona", recursive=True):
            found.append(os.path.dirname(hit))
    return found


def find_tools(configured=""):
    """{tool: path}; raises HuginError naming what's missing."""
    dirs = []
    if configured:
        dirs.append(os.path.expanduser(configured))
    if os.environ.get("HUGIN_BIN"):
        dirs.append(os.environ["HUGIN_BIN"])
    paths = {}
    for tool in TOOLS:
        for d in dirs:
            cand = os.path.join(d, tool)
            if os.access(cand, os.X_OK):
                paths[tool] = cand
                break
        else:
            on_path = shutil.which(tool)
            if on_path:
                paths[tool] = on_path
                continue
            for d in _bundle_dirs():
                cand = os.path.join(d, tool)
                if os.access(cand, os.X_OK):
                    paths[tool] = cand
                    break
    missing = [t for t in TOOLS if t not in paths]
    if missing:
        raise HuginError(
            "Hugin command-line tools not found: %s.\nInstall Hugin (see docs/02-setup-mac.md: "
            "the hugin.sourceforge.io download, or MacPorts `sudo port install hugin-app`), or "
            "set [tools] hugin_bin in ~/HDRIStitch/config.toml to the folder containing them."
            % ", ".join(missing))
    return paths


class Hugin:
    def __init__(self, workdir, configured="", log=None):
        self.tools = find_tools(configured)
        self.workdir = Path(workdir)
        self.logfile = self.workdir / "hugin.log"
        self.log = log or (lambda msg: None)

    def run(self, tool, *args, check=True):
        cmd = [self.tools[tool]] + [str(a) for a in args]
        proc = subprocess.run(cmd, cwd=self.workdir, capture_output=True, text=True,
                              errors="replace")
        with open(self.logfile, "a") as f:
            f.write("$ %s\n%s%s\n" % (" ".join(cmd), proc.stdout, proc.stderr))
        if check and proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
            raise HuginError("%s failed (exit %d):\n%s\nFull log: %s"
                             % (tool, proc.returncode, tail, self.logfile))
        return proc.stdout + proc.stderr

    def stats(self, pto):
        """Control point statistics from checkpto."""
        out = self.run("checkpto", pto, check=False)
        info = {"connected": "All images are connected" in out, "points": 0,
                "mean": None, "max": None}
        for line in out.splitlines():
            line = line.strip()
            if line.endswith("control points") and line.split()[0].isdigit():
                info["points"] = int(line.split()[0])
            elif line.startswith("Mean error"):
                info["mean"] = float(line.split(":")[1])
            elif line.startswith("Maximum"):
                info["max"] = float(line.split(":")[1])
        return info
