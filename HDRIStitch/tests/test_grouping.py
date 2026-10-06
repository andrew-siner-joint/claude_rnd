import math
from pathlib import Path

import pytest

from hdristitch.exif import Shot
from hdristitch.grouping import GroupingError, group_brackets, split_sets


def shoot(positions=8, evs=(0, -3, 3, -6, 6), base=1 / 125, gap=6.0, start=1000.0,
          fnumber=8.0):
    shots, t, n = [], start, 1
    for _ in range(positions):
        for ev in evs:
            exp = base * 2 ** ev
            shots.append(Shot(Path("DSC%05d.ARW" % n), t, exp, fnumber, 100))
            t += 0.3 + exp
            n += 1
        t += gap
    return shots


def test_manual_mode_pattern():
    groups = group_brackets(shoot())
    assert len(groups) == 8 and all(len(g) == 5 for g in groups)
    assert [s.name for s in groups[1]] == ["DSC%05d.ARW" % n for n in range(6, 11)]


def test_three_and_nine_frame_brackets():
    assert len(group_brackets(shoot(evs=(0, -2, 2)))[0]) == 3
    nine = tuple(range(-4, 5))
    assert len(group_brackets(shoot(evs=nine))[0]) == 9


def test_shuffled_input_is_sorted_by_time():
    shots = shoot()
    groups = group_brackets(list(reversed(shots)))
    assert groups[0][0].name == "DSC00001.ARW"


def test_aperture_priority_falls_back_to_time_gaps():
    shots = []
    t = 0.0
    for pos in range(8):
        base = 1 / 125 * 2 ** (pos % 3 - 1)   # metering changes per position
        for ev in (0, -2, 2):
            shots.append(Shot(Path("F%03d.ARW" % len(shots)), t, base * 2 ** ev, 8.0, 100))
            t += 0.5
        t += 5.0
    groups = group_brackets(shots)
    assert len(groups) == 8 and all(len(g) == 3 for g in groups)


def test_explicit_bracket_count_and_errors():
    shots = shoot()
    assert len(group_brackets(shots, brackets=5)) == 8
    with pytest.raises(GroupingError):
        group_brackets(shots[:-1], brackets=5)
    with pytest.raises(GroupingError, match="same exposure"):
        group_brackets(shots, brackets=10)


def test_sets():
    groups = group_brackets(shoot(positions=16))
    sets = split_sets(groups, 8)
    assert len(sets) == 2 and len(sets[1]) == 8
    with pytest.raises(GroupingError, match="multiple of 8"):
        split_sets(groups[:-1], 8)
    assert len(split_sets(groups, 0)) == 1


def test_ev100():
    s = Shot(Path("a"), 0, 1 / 125, 8.0, 100)
    assert s.ev100 == pytest.approx(math.log2(64 * 125))
