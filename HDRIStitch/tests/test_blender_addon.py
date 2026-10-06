"""Runs the Blender add-on in a real (headless) Blender via the bpy module."""
import importlib.util
import math
import os

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")
import mathutils  # noqa: E402

from hdristitch import imageio, sun  # noqa: E402

ADDON = os.path.join(os.path.dirname(__file__), "..", "blender", "hdristitch_blender.py")


@pytest.fixture(scope="module")
def addon():
    spec = importlib.util.spec_from_file_location("hdristitch_blender", ADDON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register()
    yield mod
    mod.unregister()


@pytest.fixture(scope="module")
def hdri(tmp_path_factory):
    d = tmp_path_factory.mktemp("bl")
    w, h = 512, 256
    img = np.ones((h, w, 3), np.float32) * 0.3
    img[h // 2:] = 0.1
    lon, lat = 40.0, 35.0
    x = int((lon + 180) / 360 * w)
    y = int((90 - lat) / 180 * h)
    img[y - 1:y + 2, x - 1:x + 2] = 900.0
    path = d / "test.exr"
    imageio.write_exr(path, img, metadata={"gamut": "rec709"}, latlong=True)
    info = sun.find_sun(img)
    sun.suggest(info, img)
    info["hdri_nosun"] = "test_nosun.exr"
    imageio.write_exr(d / "test_nosun.exr", sun.remove_sun(img, info), latlong=True)
    sun.write_json(d / "test_sun.json", info)
    return path, info


def _render_toward(scene, direction):
    cam = scene.camera
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = mathutils.Vector(direction).to_track_quat(
        "-Z", "Z" if abs(direction[2]) < 0.99 else "Y")
    out = os.path.join(bpy.app.tempdir or "/tmp", "hdristitch_probe.exr")
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    rgb, _ = imageio.read_exr(out)
    return float(rgb.mean())


def test_exr_header_reader(addon, hdri):
    attrs = addon.exr_attributes(str(hdri[0]))
    assert attrs["hdristitch:gamut"] == "rec709"


def test_world_and_sun_follow_rotation(addon, hdri):
    path, info = hdri
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    s = scene.hdristitch
    s.hdri_path = str(path)
    assert bpy.ops.hdristitch.build_world() == {"FINISHED"}
    nodes = scene.world.node_tree.nodes
    assert nodes["HDRI Lighting"].image.filepath.endswith("test_nosun.exr")
    assert nodes["HDRI Camera"].image.filepath.endswith("test.exr")
    sun_obj = bpy.data.objects[addon.SUN_NAME]
    assert sun_obj.data.energy == pytest.approx(info["strength"], rel=1e-5)

    scene.render.engine = "CYCLES"
    scene.cycles.samples = 2
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.render.resolution_x = scene.render.resolution_y = 4
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.view_settings.view_transform = "Standard"
    cam = bpy.data.objects.new("probe", bpy.data.cameras.new("probe"))
    cam.data.angle = math.radians(0.5)
    scene.collection.objects.link(cam)
    scene.camera = cam
    sun_obj.hide_render = True
    for rot in (0.0, 40.0, -115.0):
        s.rotation = math.radians(rot)
        bpy.context.view_layer.update()
        toward = sun_obj.matrix_world.to_3x3() @ mathutils.Vector((0, 0, 1))
        bright = _render_toward(scene, toward)
        off = _render_toward(scene, mathutils.Matrix.Rotation(math.radians(5), 3, "Z") @ toward)
        assert bright > 50 * off, (rot, bright, off)

    s.strength, s.sun_scale = 2.0, 3.0
    bpy.context.view_layer.update()
    assert sun_obj.data.energy == pytest.approx(info["strength"] * 6, rel=1e-5)


def test_reference_balls(addon):
    balls = addon.add_reference_balls(bpy.context)
    assert {b.name.split(".")[0] for b in balls} == {"Gray Ball 18%", "Chrome Ball"}
