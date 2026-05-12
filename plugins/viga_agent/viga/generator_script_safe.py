"""Safer Blender execution wrapper for VIGA tasks.

This script mirrors VIGA's static_scene generator script but avoids
CUDA device probing hangs by defaulting to CPU rendering.
Set VIGA_USE_CUDA=1 to attempt CUDA initialization.
"""

import os
import sys
import traceback

import bpy


def _read_code(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _try_enable_cuda() -> None:
    if os.getenv("VIGA_USE_CUDA", "0") != "1":
        bpy.context.scene.cycles.device = "CPU"
        return

    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "CUDA"
        prefs.get_devices()
        found_gpu = False
        for device in prefs.devices:
            if device.type == "GPU":
                device.use = True
                found_gpu = True
        bpy.context.scene.cycles.device = "GPU" if found_gpu else "CPU"
    except Exception:
        bpy.context.scene.cycles.device = "CPU"
        traceback.print_exc()


if __name__ == "__main__":
    code_fpath = sys.argv[6]
    rendering_dir = sys.argv[7] if len(sys.argv) > 7 else None
    save_blend = sys.argv[8] if len(sys.argv) > 8 else None

    code = _read_code(code_fpath)
    try:
        exec(compile(code, code_fpath, "exec"), globals(), globals())
    except Exception:
        traceback.print_exc()
        raise ValueError("Generated Blender code execution failed")

    if not rendering_dir:
        print("[INFO] No rendering directory provided, skipping rendering.")
        raise SystemExit(0)

    bpy.context.scene.render.engine = "CYCLES"
    _try_enable_cuda()
    bpy.context.scene.render.resolution_x = 512
    bpy.context.scene.render.resolution_y = 512
    bpy.context.scene.cycles.samples = 256
    bpy.context.scene.render.image_settings.color_mode = "RGB"

    for camera in bpy.data.objects:
        if camera.type != "CAMERA":
            continue
        bpy.context.scene.camera = camera
        bpy.context.scene.render.image_settings.file_format = "PNG"
        bpy.context.scene.render.filepath = os.path.join(rendering_dir, f"{camera.name}.png")
        bpy.ops.render.render(write_still=True)

    if save_blend:
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=save_blend)
