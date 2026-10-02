"""Compiling Blink kernels from Python without freezing Nuke.

In some Nuke versions, pressing a BlinkScript node's Recompile button from
Python only starts the compile; it finishes once control is back in Nuke's
event loop. Blocking the main thread to wait for it (sleeping, forceValidate,
processEvents) can then stall Nuke for good. So the compile is started and its
parameter knobs are polled on a Qt timer, with Nuke running in between.

A compiled kernel is saved once (as a copied node, keyed by a hash of the
kernel source) and every BlinkFlare node after that pastes it, which needs no
compile at all.

Outside a GUI session (no Qt event loop, e.g. tests) the timer falls back to
a plain sleep loop.
"""

import hashlib
import os
import time

import nuke

INTERVAL_MS = 250
FILE_FALLBACK_AFTER = 10.0  # seconds before also trying the load-from-file route
ERROR_GRACE = 20.0          # give up once the node has been in error this long

clock = time.time
sleep = time.sleep


# ------------------------------------------------------------------ helpers

def param_knob_name(node, param):
    """Name of the knob BlinkScript made for kernel param ``param``, or None.

    Nuke names them <Kernel>_<param>; plain and case-insensitive matches are
    accepted too in case a version differs.
    """
    knobs = node.knobs()
    if param in knobs:
        return param
    low = param.lower()
    for name in sorted(knobs):
        if name.lower() == low or name.lower().endswith("_" + low):
            return name
    return None


def _qt():
    """(QtCore, QtWidgets) when a Qt application is running, else None."""
    for module in ("PySide6", "PySide2"):
        try:
            core = __import__(module + ".QtCore", fromlist=["QtCore"])
            widgets = __import__(module + ".QtWidgets", fromlist=["QtWidgets"])
        except ImportError:
            continue
        if widgets.QApplication.instance() is None:
            return None
        return core, widgets
    return None


_queue = []
_draining = [False]


def later(ms, fn):
    """Run ``fn`` after ``ms`` milliseconds without blocking Nuke's UI."""
    qt = _qt()
    if qt is not None:
        qt[0].QTimer.singleShot(int(ms), fn)
        return
    _queue.append((ms, fn))
    if _draining[0]:
        return
    _draining[0] = True
    try:
        while _queue:
            delay, task = _queue.pop(0)
            sleep(delay / 1000.0)
            task()
    finally:
        _draining[0] = False


class Progress(object):
    """A non-modal progress dialog with Cancel (prints when there is no UI)."""

    def __init__(self, title, text):
        self._dialog = None
        self._cancelled = False
        qt = _qt()
        if qt is None:
            print("%s: %s" % (title, text))
            return
        widgets = qt[1]
        dialog = widgets.QProgressDialog(text, "Cancel", 0, 0, widgets.QApplication.activeWindow())
        dialog.setWindowTitle(title)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self._cancel)
        dialog.show()
        self._dialog = dialog

    def _cancel(self):
        self._cancelled = True

    def cancelled(self):
        return self._cancelled

    def text(self, text):
        if self._dialog is not None:
            self._dialog.setLabelText(text)

    def close(self):
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None


# ------------------------------------------------------------------ compile

class CompileResult(object):
    def __init__(self):
        self.ok = False
        self.mode = None  # immediate | deferred | timeout | cancelled | error
        self.seconds = 0.0
        self.knob = None
        self.via_file = False
        self.errors = []
        self.in_error = None

    def describe(self):
        if self.ok:
            how = "at once" if self.mode == "immediate" else "after %.1fs" % self.seconds
            return "compiled %s%s; param knob %r" % (
                how, " (via load-from-file)" if self.via_file else "", self.knob)
        text = {"timeout": "no parameter knobs after %.0fs" % self.seconds,
                "rejected": "Nuke rejected the kernel (the node stayed in error for %.0fs)"
                            % ERROR_GRACE,
                "cancelled": "cancelled after %.0fs" % self.seconds}.get(self.mode, self.mode)
        details = []
        if self.in_error is not None:
            details.append("node in error: %s" % self.in_error)
        if self.errors:
            details.append("errors: " + "; ".join(self.errors))
        return text + (" (%s)" % ", ".join(details) if details else "")


def _attempt(result, label, fn):
    try:
        fn()
    except Exception as e:  # Nuke raises plain RuntimeError from knob scripts
        result.errors.append("%s: %s" % (label, e))


def _load_from_file(node, source, result):
    path = os.path.join(cache_dir(), "pending_%d.blink" % os.getpid())
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w") as f:
        f.write(source)
    result.via_file = True
    _attempt(result, "kernelSourceFile", lambda: node["kernelSourceFile"].setValue(path))
    _attempt(result, "reloadKernelSourceFile", lambda: node["reloadKernelSourceFile"].execute())
    _attempt(result, "recompile", lambda: node["recompile"].execute())


def compile_async(node, source, param, done, timeout=300.0, progress=None,
                  label="Compiling the Blink kernel"):
    """Start compiling ``source`` on BlinkScript ``node`` and call
    ``done(result)`` once the knob for ``param`` exists, or on timeout or
    cancel. Never blocks waiting for the compile."""
    result = CompileResult()
    start = clock()
    error_since = [None]

    def in_error():
        try:
            return bool(node.hasError())
        except Exception:
            return False

    def found():
        result.knob = param_knob_name(node, param)
        return result.knob is not None

    def finish(mode):
        result.mode = mode
        result.ok = mode in ("immediate", "deferred")
        result.seconds = clock() - start
        if mode == "rejected":
            result.in_error = True
        if result.via_file and result.ok and node.knob("kernelSourceFile") is not None:
            node["kernelSourceFile"].setValue("")
        if not result.ok:
            try:
                result.in_error = node.hasError()
            except Exception:
                pass
        done(result)

    def poll():
        try:
            if found():
                return finish("deferred")
            elapsed = clock() - start
            if progress is not None and progress.cancelled():
                return finish("cancelled")
            if elapsed > timeout:
                return finish("timeout")
            if in_error():
                error_since[0] = clock() if error_since[0] is None else error_since[0]
                if clock() - error_since[0] > ERROR_GRACE:
                    return finish("rejected")
            else:
                error_since[0] = None
            if (not result.via_file and elapsed > FILE_FALLBACK_AFTER
                    and node.knob("kernelSourceFile") is not None):
                _load_from_file(node, source, result)
            if progress is not None:
                progress.text("%s... %ds" % (label, elapsed))
        except Exception as e:  # e.g. the node was deleted while we waited
            result.errors.append("poll: %s" % e)
            return finish("error")
        later(INTERVAL_MS, poll)

    _attempt(result, "kernelSource", lambda: node["kernelSource"].setValue(source))
    _attempt(result, "recompile", lambda: node["recompile"].execute())
    if found():
        finish("immediate")
    else:
        later(INTERVAL_MS, poll)


# ----------------------------------------------------------------- template

def cache_dir():
    return os.environ.get("BLINKFLARE_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".nuke", "blinkflare_cache")


def template_path(source):
    digest = hashlib.sha1(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    return os.path.join(cache_dir(), "kernel_%s.nk" % digest)


def _deselect_all():
    for n in nuke.selectedNodes():
        n.setSelected(False)


def save_template(node, context, path):
    """Save compiled BlinkScript ``node`` (living in group ``context``)."""
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with context:
        _deselect_all()
        node.setSelected(True)
        nuke.nodeCopy(path)
        node.setSelected(False)


def paste_template(path, name):
    """Paste a saved kernel into the current context and return it."""
    _deselect_all()
    nuke.nodePaste(path)
    node = nuke.selectedNode()
    node.setSelected(False)
    node.setName(name)
    return node
