"""HDRIStitch for Blender: one-click world setup for an HDRIStitch EXR.

Install: Edit > Preferences > Add-ons > (drop-down, top right) Install from
Disk..., pick this file, tick "HDRIStitch". The panel lives in Properties >
World > HDRIStitch.

What "Build World" does:
* loads the HDRI with the right colour space (read from the EXR's metadata),
* adds a Mapping node whose Z rotation is driven by the panel's Rotation,
* if a <name>_sun.json exists (written by `hdri process`): lights the scene
  with the sun-removed HDRI plus a real Sun lamp in exactly the sun's
  direction, kept aligned when you rotate the HDRI (a driver),
* optionally lets the camera see the original HDRI (with the sun in it)
  while lighting comes from the sun-removed one.
"""

bl_info = {
    "name": "HDRIStitch",
    "author": "HDRIStitch",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "Properties > World > HDRIStitch",
    "description": "Set up a world (and sun lamp) from an HDRIStitch HDRI",
    "category": "Lighting",
}

import json
import math
import os
import struct

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

WORLD_NAME = "HDRIStitch World"
SUN_NAME = "HDRI Sun"
COLORSPACES = {
    "rec709": ["Linear Rec.709", "Linear", "Linear Rec.709 (sRGB)"],
    "acescg": ["ACEScg", "Linear ACEScg", "ACES - ACEScg"],
    "rec2020": ["Linear Rec.2020", "Rec.2020 Linear"],
}


# ------------------------------------------------------------------- helpers

def exr_attributes(path):
    """String/float attributes from an OpenEXR header (no image decoding)."""
    attrs = {}
    try:
        with open(path, "rb") as f:
            data = f.read(1 << 16)
    except OSError:
        return attrs
    if data[:4] != b"\x76\x2f\x31\x01":
        return attrs
    pos = 8

    def cstr(p):
        end = data.index(b"\x00", p)
        return data[p:end].decode("latin-1"), end + 1

    try:
        while pos < len(data) and data[pos] != 0:
            name, pos = cstr(pos)
            kind, pos = cstr(pos)
            size = struct.unpack_from("<i", data, pos)[0]
            pos += 4
            raw = data[pos:pos + size]
            pos += size
            if kind == "string":
                attrs[name] = raw.decode("utf-8", "replace")
            elif kind == "float":
                attrs[name] = struct.unpack("<f", raw)[0]
            elif kind == "double":
                attrs[name] = struct.unpack("<d", raw)[0]
            elif kind == "int":
                attrs[name] = struct.unpack("<i", raw)[0]
    except (ValueError, struct.error):
        pass
    return attrs


def sidecar(path, suffix):
    base, _ = os.path.splitext(path)
    for cut in ("_nosun", "_2k", "_4k", "_1k"):
        if base.endswith(cut):
            base = base[: -len(cut)]
    candidate = base + suffix
    return candidate if os.path.exists(candidate) else None


def load_image(path, gamut):
    path = bpy.path.abspath(path)
    image = None
    for img in bpy.data.images:
        if img.filepath and bpy.path.abspath(img.filepath) == path:
            image = img
            break
    if image is None:
        image = bpy.data.images.load(path, check_existing=True)
    names = COLORSPACES.get(gamut, COLORSPACES["rec709"])
    for name in names:
        try:
            image.colorspace_settings.name = name
            break
        except TypeError:
            continue
    return image


def sun_rotation(lon_deg, lat_deg, mapping_z=0.0):
    """Sun lamp XYZ Euler (radians) for an HDRI sun at lon/lat (HDRIStitch /
    Hugin equirect convention: lon 0 = image centre, + to the right).

    Blender maps the image centre to +X, and a Mapping rotation of +a about Z
    turns the visible environment by -a."""
    return (math.radians(90.0 - lat_deg), 0.0, math.radians(90.0 - lon_deg) - mapping_z)


def _node(nodes, kind, name, location):
    node = nodes.get(name)
    if node is None or node.bl_idname != kind:
        if node is not None:
            nodes.remove(node)
        node = nodes.new(kind)
        node.name = name
    node.label = name
    node.location = location
    return node


def _driver_rotation(target, path, index, scene, expression, extra=None):
    target.driver_remove(path, index)
    fcurve = target.driver_add(path, index)
    drv = fcurve.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "rot"
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = "SCENE"
    var.targets[0].id = scene
    var.targets[0].data_path = "hdristitch.rotation"
    if extra:
        name, obj, data_path = extra
        v2 = drv.variables.new()
        v2.name = name
        v2.type = "SINGLE_PROP"
        v2.targets[0].id_type = "OBJECT"
        v2.targets[0].id = obj
        v2.targets[0].data_path = data_path
    drv.expression = expression
    return fcurve


# ------------------------------------------------------------------- build

def build_world(context, settings):
    scene = context.scene
    path = bpy.path.abspath(settings.hdri_path)
    if not path or not os.path.exists(path):
        raise RuntimeError("Pick an HDRI (.exr) first")
    attrs = exr_attributes(path)
    gamut = settings.gamut
    if gamut == "AUTO":
        gamut = attrs.get("hdristitch:gamut", "rec709")
    gamut = gamut.lower()

    sun_info = None
    sun_json = sidecar(path, "_sun.json")
    if settings.use_sun and sun_json:
        with open(sun_json) as f:
            sun_info = json.load(f)

    lighting_path = path
    if sun_info and sun_info.get("hdri_nosun"):
        candidate = os.path.join(os.path.dirname(sun_json), sun_info["hdri_nosun"])
        if os.path.exists(candidate):
            lighting_path = candidate

    world = bpy.data.worlds.get(WORLD_NAME) or bpy.data.worlds.new(WORLD_NAME)
    scene.world = world
    if bpy.app.version < (5, 0, 0):   # node trees are always on from Blender 5
        world.use_nodes = True
    nt = world.node_tree
    nodes, links = nt.nodes, nt.links
    for node in list(nodes):
        if not node.name.startswith("HDRI "):
            nodes.remove(node)

    coord = _node(nodes, "ShaderNodeTexCoord", "HDRI Coordinates", (-900, 0))
    mapping = _node(nodes, "ShaderNodeMapping", "HDRI Rotation", (-700, 0))
    mapping.vector_type = "POINT"
    light_tex = _node(nodes, "ShaderNodeTexEnvironment", "HDRI Lighting", (-450, 120))
    light_bg = _node(nodes, "ShaderNodeBackground", "HDRI Lighting Strength", (-150, 120))
    out = _node(nodes, "ShaderNodeOutputWorld", "HDRI Output", (400, 0))
    light_tex.image = load_image(lighting_path, gamut)
    links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], light_tex.inputs["Vector"])
    links.new(light_tex.outputs["Color"], light_bg.inputs["Color"])
    _driver_rotation(mapping.inputs["Rotation"], "default_value", 2, scene, "rot")
    light_bg.inputs["Strength"].driver_remove("default_value")
    _strength_driver(light_bg.inputs["Strength"], scene)

    camera_original = settings.camera_sees_original and lighting_path != path
    for name in ("HDRI Camera", "HDRI Camera Strength", "HDRI Light Path", "HDRI Mix"):
        if not camera_original and nodes.get(name):
            nodes.remove(nodes[name])
    if camera_original:
        cam_tex = _node(nodes, "ShaderNodeTexEnvironment", "HDRI Camera", (-450, -200))
        cam_bg = _node(nodes, "ShaderNodeBackground", "HDRI Camera Strength", (-150, -200))
        lp = _node(nodes, "ShaderNodeLightPath", "HDRI Light Path", (-150, 400))
        mix = _node(nodes, "ShaderNodeMixShader", "HDRI Mix", (150, 0))
        cam_tex.image = load_image(path, gamut)
        links.new(mapping.outputs["Vector"], cam_tex.inputs["Vector"])
        links.new(cam_tex.outputs["Color"], cam_bg.inputs["Color"])
        cam_bg.inputs["Strength"].driver_remove("default_value")
        _strength_driver(cam_bg.inputs["Strength"], scene)
        links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
        links.new(light_bg.outputs["Background"], mix.inputs[1])
        links.new(cam_bg.outputs["Background"], mix.inputs[2])
        links.new(mix.outputs["Shader"], out.inputs["Surface"])
    else:
        links.new(light_bg.outputs["Background"], out.inputs["Surface"])

    sun_obj = bpy.data.objects.get(SUN_NAME)
    if sun_info:
        sun_obj = _build_sun(context, settings, sun_info, sun_obj)
    elif sun_obj is not None:
        sun_obj.hide_render = sun_obj.hide_viewport = True
    scene.update_tag()
    context.view_layer.update()
    return {"lighting": lighting_path, "camera": path if camera_original else lighting_path,
            "gamut": gamut, "sun": sun_obj.name if sun_info else None}


def _strength_driver(socket, scene):
    fcurve = socket.driver_add("default_value")
    drv = fcurve.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "s"
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = "SCENE"
    var.targets[0].id = scene
    var.targets[0].data_path = "hdristitch.strength"
    drv.expression = "s"


def _build_sun(context, settings, info, sun_obj):
    if sun_obj is None or sun_obj.type != "LIGHT" or sun_obj.data.type != "SUN":
        data = bpy.data.lights.new(SUN_NAME, "SUN")
        sun_obj = bpy.data.objects.new(SUN_NAME, data)
        context.scene.collection.objects.link(sun_obj)
    sun_obj.hide_render = sun_obj.hide_viewport = False
    light = sun_obj.data
    light.angle = math.radians(info.get("angle_deg", 0.53))
    light.color = [max(0.0, c) for c in info.get("color", (1.0, 1.0, 1.0))]
    sun_obj["hdri_strength_measured"] = float(info.get("strength", 1.0))
    sun_obj["hdri_clipped"] = bool(info.get("clipped", False))
    sun_obj["hdri_strength_source"] = info.get("strength_source", "")
    light.driver_remove("energy")
    fcurve = light.driver_add("energy")
    drv = fcurve.driver
    drv.type = "SCRIPTED"
    for name, id_type, idb, data_path in (
            ("base", "OBJECT", sun_obj, '["hdri_strength_measured"]'),
            ("k", "SCENE", context.scene, "hdristitch.sun_scale"),
            ("s", "SCENE", context.scene, "hdristitch.strength")):
        var = drv.variables.new()
        var.name = name
        var.type = "SINGLE_PROP"
        var.targets[0].id_type = id_type
        var.targets[0].id = idb
        var.targets[0].data_path = data_path
    drv.expression = "base * k * s"

    rx, _, rz = sun_rotation(info["lon_deg"], info["lat_deg"])
    sun_obj.rotation_mode = "XYZ"
    sun_obj["hdri_base_z"] = rz
    sun_obj.rotation_euler = (rx, 0.0, rz)
    _driver_rotation(sun_obj, "rotation_euler", 2, context.scene, "base - rot",
                     extra=("base", sun_obj, '["hdri_base_z"]'))
    return sun_obj


def add_reference_balls(context):
    """An 18% gray ball and a chrome ball at the 3D cursor, for matching the
    HDRI lighting against photographed reference balls."""
    loc = context.scene.cursor.location
    made = []
    for name, offset, metal, rough, value in (("Gray Ball 18%", -0.15, 0.0, 0.5, 0.18),
                                              ("Chrome Ball", 0.15, 1.0, 0.0, 0.9)):
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        context.scene.collection.objects.link(obj)
        import bmesh

        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=0.1)
        for f in bm.faces:
            f.smooth = True
        bm.to_mesh(mesh)
        bm.free()
        obj.location = (loc.x + offset, loc.y, loc.z)
        mat = bpy.data.materials.new(name)
        if bpy.app.version < (5, 0, 0):
            mat.use_nodes = True
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (value, value, value, 1.0)
        bsdf.inputs["Metallic"].default_value = metal
        bsdf.inputs["Roughness"].default_value = rough
        obj.data.materials.append(mat)
        made.append(obj)
    return made


# ------------------------------------------------------------------- UI

def _refresh(self, context):
    # the world node and sun lamp follow these through drivers; tag the scene
    # so the drivers re-evaluate right away (Python edits don't always)
    self.id_data.update_tag()


class HDRIStitchSettings(bpy.types.PropertyGroup):
    hdri_path: StringProperty(name="HDRI", subtype="FILE_PATH",
                              description="The HDRIStitch .exr (not the _nosun one)")
    rotation: FloatProperty(name="Rotation", subtype="ANGLE", default=0.0, update=_refresh,
                            description="Turns the HDRI (and the sun lamp with it)")
    strength: FloatProperty(name="Strength", default=1.0, min=0.0, soft_max=10.0,
                            update=_refresh,
                            description="Scales the HDRI and the sun lamp together")
    sun_scale: FloatProperty(name="Sun Calibration", default=1.0, min=0.0, soft_max=10.0,
                             update=_refresh,
                             description="Extra scale on the sun lamp only (calibrate a "
                                         "clipped sun against your gray ball)")
    use_sun: BoolProperty(name="Use Sun Lamp", default=True,
                          description="Light with the sun-removed HDRI plus a Sun lamp, "
                                      "if <name>_sun.json exists")
    camera_sees_original: BoolProperty(
        name="Camera Sees Original", default=True,
        description="Camera rays see the HDRI with its sun; lighting uses the sun-removed one")
    gamut: EnumProperty(name="Colour Space", default="AUTO", items=[
        ("AUTO", "Auto", "From the EXR's metadata"),
        ("rec709", "Linear Rec.709", ""), ("acescg", "ACEScg", ""),
        ("rec2020", "Linear Rec.2020", "")])


class HDRISTITCH_OT_build(bpy.types.Operator):
    bl_idname = "hdristitch.build_world"
    bl_label = "Build World"
    bl_description = "Create/update the HDRIStitch world (and sun lamp)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            info = build_world(context, context.scene.hdristitch)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        msg = "HDRI world built"
        if info["sun"]:
            msg += " with sun lamp"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class HDRISTITCH_OT_balls(bpy.types.Operator):
    bl_idname = "hdristitch.reference_balls"
    bl_label = "Add Gray + Chrome Balls"
    bl_description = "Reference spheres at the 3D cursor for lighting calibration"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        add_reference_balls(context)
        return {"FINISHED"}


class HDRISTITCH_PT_panel(bpy.types.Panel):
    bl_label = "HDRIStitch"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "world"

    def draw(self, context):
        s = context.scene.hdristitch
        col = self.layout.column()
        col.prop(s, "hdri_path")
        col.prop(s, "gamut")
        col.prop(s, "use_sun")
        sub = col.column()
        sub.enabled = s.use_sun
        sub.prop(s, "camera_sees_original")
        col.operator(HDRISTITCH_OT_build.bl_idname, icon="WORLD")
        col.separator()
        col.prop(s, "rotation")
        col.prop(s, "strength")
        sun = bpy.data.objects.get(SUN_NAME)
        if sun is not None and not sun.hide_render:
            col.prop(s, "sun_scale")
            if sun.get("hdri_clipped"):
                col.label(text="Sun was clipped: calibrate it", icon="ERROR")
        col.separator()
        col.operator(HDRISTITCH_OT_balls.bl_idname, icon="SHADING_RENDERED")


CLASSES = (HDRIStitchSettings, HDRISTITCH_OT_build, HDRISTITCH_OT_balls, HDRISTITCH_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hdristitch = PointerProperty(type=HDRIStitchSettings)


def unregister():
    del bpy.types.Scene.hdristitch
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
