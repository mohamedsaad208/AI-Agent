"""Generates a high-quality animated GIF demo showing AI Code Engineer in action."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 900, 520
BG_DARK = (15, 23, 42)         # Slate 900
HEADER_BG = (30, 41, 59)       # Slate 800
PANEL_BG = (17, 24, 39)        # Gray 900
ACCENT_CYAN = (56, 189, 248)    # Sky 400
ACCENT_PURPLE = (168, 85, 247) # Purple 500
GREEN_TEXT = (74, 222, 128)    # Green 400
RED_TEXT = (248, 113, 113)     # Red 400
WHITE_TEXT = (248, 250, 252)
GRAY_TEXT = (148, 163, 184)
BORDER_COL = (51, 65, 85)

def get_font(size=14, bold=False):
    try:
        # Try standard windows fonts
        name = "consola.ttf" if not bold else "consolab.ttf"
        return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
    except Exception:
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/arial{'bd' if bold else ''}.ttf", size)
        except Exception:
            return ImageFont.load_default()

def draw_window_frame(draw):
    # Base background
    draw.rectangle([(0, 0), (WIDTH, HEIGHT)], fill=BG_DARK)
    
    # Title bar
    draw.rectangle([(0, 0), (WIDTH, 42)], fill=HEADER_BG)
    draw.line([(0, 42), (WIDTH, 42)], fill=BORDER_COL, width=1)
    
    # Window controls (mac style dots)
    draw.ellipse([(16, 15), (28, 27)], fill=(239, 68, 68))
    draw.ellipse([(36, 15), (48, 27)], fill=(234, 179, 8))
    draw.ellipse([(56, 15), (68, 27)], fill=(34, 197, 94))
    
    # App Title
    font = get_font(13, bold=True)
    draw.text((WIDTH // 2 - 90, 13), "AI CODE ENGINEER - WebApp v1.0", fill=WHITE_TEXT, font=font)
    
    # Sidebar outline
    draw.rectangle([(0, 43), (220, HEIGHT)], fill=(13, 19, 33))
    draw.line([(220, 43), (220, HEIGHT)], fill=BORDER_COL, width=1)
    
    # Sidebar items
    f_side = get_font(12, bold=False)
    f_side_b = get_font(12, bold=True)
    draw.text((16, 60), "PROJECTS", fill=GRAY_TEXT, font=get_font(11, bold=True))
    draw.text((20, 85), "📁 demo_repo (main)", fill=ACCENT_CYAN, font=f_side_b)
    draw.text((28, 110), "📄 calculator.py", fill=WHITE_TEXT, font=f_side)
    draw.text((28, 130), "📄 test_calculator.py", fill=GRAY_TEXT, font=f_side)
    
    draw.text((16, 170), "PROVIDER & MODEL", fill=GRAY_TEXT, font=get_font(11, bold=True))
    draw.rectangle([(16, 190), (204, 222)], fill=(30, 41, 59), outline=ACCENT_PURENT if 'ACCENT_PURENT' in globals() else BORDER_COL)
    draw.text((24, 198), "🤖 Ollama / qwen2.5-coder", fill=WHITE_TEXT, font=get_font(11))
    
    draw.text((16, 250), "SECURITY GATES", fill=GRAY_TEXT, font=get_font(11, bold=True))
    draw.text((20, 272), "🔒 Zero-Trust: Active", fill=GREEN_TEXT, font=get_font(11))
    draw.text((20, 292), "⚡ Auto-Apply: ON", fill=(251, 191, 36), font=get_font(11))
    draw.text((20, 312), "🧪 Auto-Verify: ON", fill=ACCENT_CYAN, font=get_font(11))

def create_frame(step, progress=0.0):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_window_frame(draw)
    
    f_code = get_font(13)
    f_title = get_font(14, bold=True)
    f_chat = get_font(13)
    
    # Main Content Area
    cx = 240
    cy = 60
    
    # 1. User Prompt Bubble
    draw.rectangle([(cx, cy), (WIDTH - 24, cy + 50)], fill=(30, 41, 59), outline=BORDER_COL)
    draw.text((cx + 14, cy + 8), "USER REQUEST", fill=ACCENT_CYAN, font=get_font(11, bold=True))
    prompt_text = "Fix add in calculator.py so it adds two numbers and verify with pytest"
    if step == 0:
        chars = int(len(prompt_text) * progress)
        displayed_prompt = prompt_text[:chars] + ("_" if int(progress * 10) % 2 == 0 else "")
    else:
        displayed_prompt = prompt_text
    draw.text((cx + 14, cy + 26), displayed_prompt, fill=WHITE_TEXT, font=f_chat)
    
    if step >= 1:
        # Step 1: Agent Thinking & AST Map
        cy += 65
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 42)], fill=(17, 24, 39), outline=BORDER_COL)
        draw.text((cx + 14, cy + 12), "🔍 AST Indexer: Parsed 2 symbols in calculator.py (`add`, `subtract`)", fill=GRAY_TEXT, font=f_code)
        draw.text((WIDTH - 120, cy + 12), "[0.04s]", fill=GRAY_TEXT, font=f_code)
        
    if step >= 2:
        # Step 2: Proposing Cryptographic Diff
        cy += 54
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 180)], fill=(15, 23, 42), outline=BORDER_COL)
        
        # Diff header
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 30)], fill=(30, 41, 59))
        draw.text((cx + 12, cy + 8), "✍️ PROPOSED DIFF  |  SHA-256: 7f8c92a1... (Verified)", fill=ACCENT_PURPLE, font=get_font(11, bold=True))
        
        # Diff content lines
        diff_lines = [
            ("--- a/examples/demo_repo/calculator.py", GRAY_TEXT),
            ("+++ b/examples/demo_repo/calculator.py", GRAY_TEXT),
            ("@@ -2,4 +2,4 @@ def add(a, b):", ACCENT_CYAN),
            ("-    return a - b  # Bug: wrong arithmetic operator", RED_TEXT),
            ("+    return a + b  # Fixed: correctly adds operands", GREEN_TEXT),
        ]
        dy = cy + 40
        for line, col in diff_lines:
            if col == RED_TEXT:
                draw.rectangle([(cx + 4, dy - 2), (WIDTH - 28, dy + 18)], fill=(127, 29, 29, 100))
            elif col == GREEN_TEXT:
                draw.rectangle([(cx + 4, dy - 2), (WIDTH - 28, dy + 18)], fill=(20, 83, 45, 100))
            draw.text((cx + 14, dy), line, fill=col, font=f_code)
            dy += 24

    if step >= 3:
        # Step 3: Automated Verification
        cy += 194
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 85)], fill=(17, 24, 39), outline=(34, 197, 94) if step == 4 else BORDER_COL)
        draw.text((cx + 14, cy + 10), "⚙️ RUNNING TEST SUITE: `python -m pytest tests/`", fill=WHITE_TEXT, font=get_font(12, bold=True))
        
        if step == 3:
            draw.text((cx + 14, cy + 34), "⏳ Executing test runner in sandboxed workspace...", fill=ACCENT_CYAN, font=f_code)
            # Progress bar
            bar_w = int((WIDTH - cx - 50) * progress)
            draw.rectangle([(cx + 14, cy + 60), (cx + 14 + bar_w, cy + 68)], fill=ACCENT_CYAN)
        elif step == 4:
            draw.text((cx + 14, cy + 34), "collected 2 items", fill=GRAY_TEXT, font=f_code)
            draw.text((cx + 14, cy + 54), "tests/test_calculator.py::test_add PASSED               [100%]", fill=GREEN_TEXT, font=f_code)
            
            # Bottom status banner
            draw.rectangle([(cx, HEIGHT - 45), (WIDTH - 24, HEIGHT - 12)], fill=(20, 83, 45), outline=(34, 197, 94))
            draw.text((cx + 16, HEIGHT - 35), "✅ ALL CHECKS PASSED: 2 passed, 0 failed in 0.42s (Proposal applied)", fill=WHITE_TEXT, font=get_font(12, bold=True))
            
    return img

def main():
    frames = []
    
    # 1. Typing prompt (12 frames)
    for i in range(12):
        frames.append(create_frame(step=0, progress=(i + 1) / 12))
    
    # Pause on typed prompt (4 frames)
    for _ in range(4):
        frames.append(create_frame(step=0, progress=1.0))
        
    # 2. Agent parsing & AST (6 frames)
    for _ in range(6):
        frames.append(create_frame(step=1))
        
    # 3. Proposing Diff (12 frames)
    for _ in range(12):
        frames.append(create_frame(step=2))
        
    # 4. Running tests (8 frames)
    for i in range(8):
        frames.append(create_frame(step=3, progress=(i + 1) / 8))
        
    # 5. Success State & Green Checks (24 frames)
    for _ in range(24):
        frames.append(create_frame(step=4))
        
    out_path = Path(__file__).resolve().parents[1] / "assets" / "demo.gif"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=120,
        loop=0,
        optimize=True
    )
    print(f"Demo GIF created successfully at: {out_path} ({len(frames)} frames)")

if __name__ == "__main__":
    main()
