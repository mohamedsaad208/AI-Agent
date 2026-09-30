"""Records live screenshots from the Desktop WebApp in --fake mode and builds animated GIFs."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_code_engineer.webapp.fake import FakeController
from ai_code_engineer.webapp.server import serve

def main():
    frames_dir = ROOT / ".tmp_frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    print("1. Starting WebApp server with FakeController (Zero project disk modification)...")
    controller = FakeController()
    server, url, token = serve(controller, port=0)
    print(f"   Server ready at {url}")

    try:
        print("2. Launching headless Chrome via DevTools Protocol to capture live frames...")
        capture_script = ROOT / "tools" / "capture_frames.mjs"
        res = subprocess.run(["node", str(capture_script), url, str(frames_dir)], capture_output=True, text=True)
        print(res.stdout)
        if res.returncode != 0:
            print("ERROR capturing frames:", res.stderr)
            return 1
    finally:
        print("3. Shutting down WebApp server...")
        server.shutdown()

    # Collect captured frames
    frame_files = sorted(frames_dir.glob("*.png"))
    if not frame_files:
        print("No frames captured!")
        return 1

    print(f"4. Processing {len(frame_files)} captured frames into animated GIF...")
    durations = [
        2600, # 01_changes_card
        2200, # 02_diff_view
        1200, # 03_diff_now_tab
        1600, # 04_diff_back_tab
        2600, # 05_tasks_tab
        2000, # 06_seq_running
        2800, # 07_seq_step3_advanced
    ]

    images = []
    for f in frame_files:
        img = Image.open(f).convert("RGBA")
        # Ensure crisp display by converting to RGB with high-quality adaptive palette
        rgb = Image.new("RGB", img.size, (15, 23, 42))
        rgb.paste(img, mask=img.split()[3])
        # Quantize to adaptive 256 colors for crisp text
        quant = rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        images.append(quant)

    # Save to assets/demo-webapp.gif
    out_webapp = ROOT / "assets" / "demo-webapp.gif"
    images[0].save(
        out_webapp,
        save_all=True,
        append_images=images[1:],
        duration=durations[:len(images)],
        loop=0,
        optimize=True
    )
    print(f"   Created {out_webapp} ({out_webapp.stat().st_size // 1024} KB)")

    # Also update assets/demo.gif
    out_demo = ROOT / "assets" / "demo.gif"
    images[0].save(
        out_demo,
        save_all=True,
        append_images=images[1:],
        duration=durations[:len(images)],
        loop=0,
        optimize=True
    )
    print(f"   Created {out_demo} ({out_demo.stat().st_size // 1024} KB)")

    # Cleanup temporary directories
    shutil.rmtree(frames_dir, ignore_errors=True)
    shutil.rmtree(ROOT / ".tmp_chrome_rec", ignore_errors=True)
    print("5. Recording complete! All GIFs updated with actual UI captures.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
