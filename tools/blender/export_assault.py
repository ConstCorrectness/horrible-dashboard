#!/usr/bin/env python3
"""
Quick exporter for hd_assault map from Blender to Horrible Assault.

Usage from terminal:
    blender -b assets/maps/hd_assault.blend -P tools/blender/export_assault.py

Or run directly from inside Blender's 'Scripting' workspace.
"""

import os
import shutil
from pathlib import Path

try:
    import bpy
except ImportError:
    # If run outside blender via python, execute blender in background
    import subprocess
    import sys
    repo_root = Path(__file__).resolve().parents[2]
    blend_file = repo_root / "assets/maps/hd_assault.blend"
    cmd = ["blender", "-b", str(blend_file), "-P", str(Path(__file__).resolve())]
    print(f"Running: {' '.join(cmd)}")
    ret = subprocess.run(cmd)
    sys.exit(ret.returncode)

def export_map():
    repo_root = Path("/home/horrible/horrible-dashboard")
    glb_backend_path = repo_root / "backend/modules/hassault/maps/hd_assault.glb"
    glb_web_path = repo_root / "apps/web/public/hd_assault.glb"

    glb_backend_path.parent.mkdir(parents=True, exist_ok=True)
    glb_web_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Exporting GLB to: {glb_backend_path}")
    bpy.ops.export_scene.gltf(
        filepath=str(glb_backend_path),
        export_format='GLB',
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials='EXPORT',
        export_lights=True,
        export_cameras=True
    )

    shutil.copyfile(glb_backend_path, glb_web_path)
    print(f"Copied GLB to web: {glb_web_path}")
    print("Export complete!")

if __name__ == "__main__":
    export_map()
