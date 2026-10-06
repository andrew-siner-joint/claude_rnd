"""Build a synthetic shoot folder: 8 positions x N brackets of DNG raws."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import synth  # noqa: E402


def make_shoot(folder, scene, brackets=(0, -3, 3, -6, 6), size=(1200, 800), circle=760,
               k1=0.03, base=0.25, t_base=1 / 125, rig=synth.RIG, tilt=(0.0, 0.0),
               start="2026:10:06 10:00:00", seed=0, illuminant_gain=(1.0, 1.0, 1.0),
               prefix="DSC", first=1, jitter=0.0):
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(seed)
    t0 = datetime.strptime(start, "%Y:%m:%d %H:%M:%S")
    n = first
    clock = 0.0
    tilt_r = synth.rot(0, tilt[0], tilt[1])
    for pos, (yaw, pitch) in enumerate(rig):
        roll = 0.0
        if jitter:  # rig play: each position lands slightly off its detent
            yaw, pitch, roll = (v + rng.normal(0, jitter) for v in (yaw, pitch, roll))
        view = synth.fisheye_view(scene, yaw, pitch, roll, size=size, circle=circle, k1=k1,
                                  world=tilt_r)
        cam = synth.scene_to_camera(view) * np.asarray(illuminant_gain)
        for ev in brackets:
            when = t0 + timedelta(seconds=clock)
            stamp = when.strftime("%Y:%m:%d %H:%M:%S")
            sub = "%02d" % int((clock % 1) * 100)
            synth.write_dng(os.path.join(folder, "%s%05d.dng" % (prefix, n)), cam,
                            exposure=base * 2 ** ev, shutter=t_base * 2 ** ev, when=stamp,
                            subsec=sub, noise=0.0003, rng=rng)
            n += 1
            clock += 0.4 + t_base * 2 ** ev
        clock += 6.0
    return n - first


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    sc = synth.make_scene(2048)
    np.save(os.path.join(out, "..", "scene.npy"), sc)
    print(make_shoot(out, sc))
