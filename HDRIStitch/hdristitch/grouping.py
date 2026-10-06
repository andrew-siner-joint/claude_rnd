"""Split a folder of raws into brackets (one per camera position) and the
brackets into HDRI sets."""
import math
from datetime import datetime


class GroupingError(RuntimeError):
    pass


def _stops(shot):
    """Exposure in thirds of a stop, rounded, for pattern comparison."""
    return int(round(math.log2(shot.exposure) * 3))


def _pattern_size(shots, max_size=15):
    """Smallest N such that every run of N frames holds N distinct exposures
    and all runs share the same set of exposures (manual-mode bracketing)."""
    n = len(shots)
    for size in range(1, min(n, max_size) + 1):
        if n % size:
            continue
        keys = None
        ok = True
        for start in range(0, n, size):
            group = [_stops(s) for s in shots[start:start + size]]
            if len(set(group)) != size:
                ok = False
                break
            key = tuple(sorted(group))
            if keys is None:
                keys = key
            elif key != keys:
                ok = False
                break
        if ok:
            return size
    return None


def _gap_groups(shots, gap):
    groups = [[shots[0]]]
    for prev, cur in zip(shots, shots[1:]):
        idle = cur.time - (prev.time + prev.exposure_time)
        if idle > gap:
            groups.append([])
        groups[-1].append(cur)
    return groups


def _describe(groups):
    lines = []
    for i, group in enumerate(groups):
        when = datetime.fromtimestamp(group[0].time).strftime("%H:%M:%S") if group[0].time else "?"
        expo = ", ".join(_fmt_time(s.exposure_time) for s in group)
        lines.append("  bracket %2d  %s  %-12s .. %-12s  [%s]"
                     % (i + 1, when, group[0].name, group[-1].name, expo))
    return "\n".join(lines)


def _fmt_time(t):
    return "1/%d" % round(1 / t) if t < 0.5 else "%gs" % round(t, 2)


def group_brackets(shots, brackets="auto", gap_seconds=2.0):
    """Return a list of brackets (lists of Shot), in capture order."""
    if not shots:
        raise GroupingError("No raw files found.")
    shots = sorted(shots, key=lambda s: (s.time, s.name))
    if brackets not in ("auto", "", None, 0):
        size = int(brackets)
        if len(shots) % size:
            raise GroupingError(
                "%d frames is not a multiple of %d brackets. Missing or extra frames?"
                % (len(shots), size))
        groups = [shots[i:i + size] for i in range(0, len(shots), size)]
    else:
        size = _pattern_size(shots)
        if size is not None:
            groups = [shots[i:i + size] for i in range(0, len(shots), size)]
        elif any(s.time for s in shots):
            groups = _gap_groups(shots, gap_seconds)
            sizes = {len(g) for g in groups}
            if len(sizes) != 1:
                raise GroupingError(
                    "Could not split the frames into equal brackets (the exposure pattern "
                    "changes and the time gaps give groups of %s frames). Shoot in M mode, or "
                    "pass --brackets N.\n%s" % (sorted(sizes), _describe(groups)))
        else:
            raise GroupingError("Could not detect the bracket size; pass --brackets N.")
    for group in groups:
        stops = [_stops(s) for s in group]
        if len(set(stops)) != len(stops):
            raise GroupingError("A bracket contains two frames with the same exposure:\n"
                                + _describe([group]))
    return groups


def split_sets(groups, positions=8):
    """Brackets -> HDRI sets of `positions` brackets each (0 = one set)."""
    if not positions:
        return [groups]
    if len(groups) % positions:
        raise GroupingError(
            "Found %d brackets, which is not a multiple of %d positions per HDRI. Check for a "
            "missing or extra position, or pass --positions N.\n%s"
            % (len(groups), positions, _describe(groups)))
    return [groups[i:i + positions] for i in range(0, len(groups), positions)]


def summary(sets):
    lines = []
    for k, hdri in enumerate(sets):
        frames = sum(len(g) for g in hdri)
        lines.append("HDRI set %d: %d positions x %d brackets (%d frames)"
                     % (k + 1, len(hdri), len(hdri[0]), frames))
        lines.append(_describe(hdri))
    return "\n".join(lines)
