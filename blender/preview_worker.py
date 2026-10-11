"""
A Blender that stays open for the 3D studio's previews (video_studio.py):
each line on stdin is a job {"argv": [render3d.py arguments]}; the scene is
rebuilt from an empty file and rendered, and "@@DONE" or "@@FAIL <error>"
is printed. Staying open keeps Eevee's compiled shaders, so a preview takes
seconds instead of a fresh start's 15 or more.

    blender -b --factory-startup -P blender/preview_worker.py
"""

import json
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import render3d  # noqa: E402

print("@@READY", flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        job = json.loads(line)
        bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
        render3d.run(job["argv"])
        print("@@DONE", flush=True)
    except Exception as e:
        traceback.print_exc()
        print("@@FAIL " + str(e).replace("\n", " "), flush=True)
