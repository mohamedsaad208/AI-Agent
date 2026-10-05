"""Generates fresh, high-end animated GIFs for the Desktop WebApp and Complex Self-Healing Workflows."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 960, 580
BG_DARK = (11, 15, 25)           # Deep slate
HEADER_BG = (20, 27, 45)         # Slate header
SIDEBAR_BG = (15, 21, 35)        # Dark navy rail
PANEL_BG = (17, 24, 39)          # Card gray
BORDER_COL = (38, 48, 68)        # Clean borders
BORDER_LIGHT = (55, 68, 92)

# Semantic Colors
ACCENT_CYAN = (56, 189, 248)     # Sky 400
ACCENT_PURPLE = (168, 85, 247)  # Violet 400
ACCENT_EMERALD = (16, 185, 129) # Emerald 500
ACCENT_AMBER = (245, 158, 11)   # Amber 500
ACCENT_RED = (239, 68, 68)      # Rose 500
WHITE_TEXT = (248, 250, 252)
GRAY_TEXT = (148, 163, 184)
MUTED_TEXT = (100, 116, 139)

def get_font(size=12, bold=False, mono=False):
    try:
        if mono:
            name = "consolab.ttf" if bold else "consola.ttf"
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        else:
            name = "segoeuib.ttf" if bold else "segoeui.ttf"
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
    except Exception:
        try:
            name = "arialbd.ttf" if bold else "arial.ttf"
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except Exception:
            return ImageFont.load_default()

def draw_top_bar(draw, title="AI CODE ENGINEER — Autonomous Software Agent", stage="PENDING", model="Google Gemini 3.8 Flash"):
    # Window Frame & Header
    draw.rectangle([(0, 0), (WIDTH, HEIGHT)], fill=BG_DARK)
    draw.rectangle([(0, 0), (WIDTH, 44)], fill=HEADER_BG)
    draw.line([(0, 44), (WIDTH, 44)], fill=BORDER_COL, width=1)
    
    # OS Traffic Light Dots
    draw.ellipse([(14, 16), (24, 26)], fill=(239, 68, 68))
    draw.ellipse([(32, 16), (42, 26)], fill=(245, 158, 11))
    draw.ellipse([(50, 16), (60, 26)], fill=(16, 185, 129))
    
    # Window Title
    f_title = get_font(12, bold=True)
    draw.text((75, 14), title, fill=WHITE_TEXT, font=f_title)
    
    # Stage Pill in Header
    stage_colors = {
        "PENDING": (100, 116, 139),
        "PLANNING": ACCENT_CYAN,
        "REVIEWING": ACCENT_PURPLE,
        "EXECUTING": ACCENT_AMBER,
        "VERIFYING": ACCENT_CYAN,
        "DONE": ACCENT_EMERALD,
    }
    sc = stage_colors.get(stage, ACCENT_CYAN)
    draw.rectangle([(460, 11), (555, 33)], fill=(25, 35, 55), outline=sc)
    draw.text((472, 14), f"● {stage}", fill=sc, font=get_font(10, bold=True))
    
    # Model Badge
    draw.rectangle([(565, 11), (745, 33)], fill=(20, 28, 45), outline=BORDER_COL)
    draw.text((575, 14), f"🤖 {model}", fill=ACCENT_CYAN, font=get_font(10, bold=True))
    
    # Steer & Stop Quick Buttons
    draw.rectangle([(755, 11), (885, 33)], fill=(25, 35, 55), outline=BORDER_COL)
    draw.text((763, 14), "🧭 Steer: active...", fill=GRAY_TEXT, font=get_font(10))
    
    draw.rectangle([(895, 11), (946, 33)], fill=(185, 28, 28), outline=ACCENT_RED)
    draw.text((905, 14), "⏹ Stop", fill=WHITE_TEXT, font=get_font(10, bold=True))

def create_webapp_frame(step, progress=0.0):
    """Generates a dynamic 3-column WebApp walkthrough showing real-time agent execution."""
    stages = ["PLANNING", "PLANNING", "REVIEWING", "VERIFYING", "DONE"]
    stage = stages[min(step, len(stages) - 1)]
    
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_top_bar(draw, "AI CODE ENGINEER — Enterprise WebApp", stage=stage, model="Google Gemini 3.8 Flash")
    
    f_ui = get_font(11)
    f_ui_b = get_font(11, bold=True)
    f_mono = get_font(11, mono=True)
    f_mono_b = get_font(11, bold=True, mono=True)
    
    # -------------------------------------------------------------
    # COLUMN 1: Left Rail (Plan & Tasks Checklist) - Width: 220px
    # -------------------------------------------------------------
    draw.rectangle([(0, 45), (220, HEIGHT)], fill=SIDEBAR_BG)
    draw.line([(220, 45), (220, HEIGHT)], fill=BORDER_COL, width=1)
    
    draw.text((14, 58), "ACTIVE PROJECT", fill=MUTED_TEXT, font=get_font(9, bold=True))
    draw.rectangle([(10, 75), (210, 105)], fill=(22, 31, 50), outline=ACCENT_CYAN)
    draw.text((18, 83), "📁 demo_repo (main 🌿)", fill=WHITE_TEXT, font=f_ui_b)
    
    draw.text((14, 120), "PLAN CHECKLIST", fill=MUTED_TEXT, font=get_font(9, bold=True))
    
    # Checklist Steps based on current progress
    chk_steps = [
        ("1. AST Index & Code Graph", step >= 1),
        ("2. Synthesize Fix in calc.py", step >= 2),
        ("3. Verify with Pytest Runner", step >= 3),
        ("4. Cryptographic Proof Commit", step >= 4),
    ]
    cy = 138
    for label, is_done in chk_steps:
        if is_done:
            icon = "✓"
            color = ACCENT_EMERALD
        elif (label.startswith("1") and step == 0) or (label.startswith("2") and step == 1) or (label.startswith("3") and step == 2):
            icon = "●"
            color = ACCENT_CYAN
        else:
            icon = "○"
            color = MUTED_TEXT
        draw.text((16, cy), f"{icon}  {label}", fill=color, font=f_ui)
        cy += 24
        
    # Memory & Compass Box
    my = 260
    draw.rectangle([(10, my), (210, my + 130)], fill=(18, 25, 42), outline=BORDER_COL)
    draw.text((16, my + 10), "🧠 PROJECT COMPASS", fill=ACCENT_PURPLE, font=get_font(9, bold=True))
    draw.text((16, my + 30), "Goal: Calculator Bugfix", fill=WHITE_TEXT, font=f_ui)
    draw.text((16, my + 50), "Tokens: 1,120 / 1,500", fill=GRAY_TEXT, font=f_ui)
    draw.text((16, my + 70), "Memory: .agent/memory/", fill=MUTED_TEXT, font=get_font(9, mono=True))
    draw.text((16, my + 95), "🛡️ Zero-Trust Gate: Sealed", fill=ACCENT_EMERALD, font=get_font(9, bold=True))
    
    # Bottom shortcuts info
    draw.text((14, HEIGHT - 35), "Hotkeys: Esc Stop  Tab Rail  ? Help", fill=MUTED_TEXT, font=get_font(9))
    
    # -------------------------------------------------------------
    # COLUMN 2: Center Workspace (Live Event Stream & Chat) - Width: 460px
    # -------------------------------------------------------------
    cx = 221
    cw = 460
    draw.rectangle([(cx, 45), (cx + cw, HEIGHT)], fill=BG_DARK)
    draw.line([(cx + cw, 45), (cx + cw, HEIGHT)], fill=BORDER_COL, width=1)
    
    # User message bubble (Right aligned)
    uy = 58
    bubble_w = 410
    draw.rectangle([(cx + 25, uy), (cx + 25 + bubble_w, uy + 50)], fill=(28, 38, 62), outline=BORDER_LIGHT)
    draw.text((cx + 38, uy + 6), "👤 Developer Request", fill=ACCENT_CYAN, font=get_font(10, bold=True))
    user_req = "Fix add() in calculator.py so it adds two numbers, verify with pytest"
    draw.text((cx + 38, uy + 24), user_req, fill=WHITE_TEXT, font=f_ui)
    
    # Event Stream entries
    ey = uy + 62
    
    # Step 1: AST Code Graph Analysis
    if step >= 1:
        draw.rectangle([(cx + 15, ey), (cx + cw - 15, ey + 42)], fill=(16, 23, 38), outline=BORDER_COL)
        draw.text((cx + 25, ey + 6), "[ANALYSIS]  AST Code Graph", fill=ACCENT_CYAN, font=get_font(9, bold=True))
        draw.text((cx + 25, ey + 22), "Resolved symbols: `calculator.py:add` (line 2) • Callers: 1 test", fill=GRAY_TEXT, font=f_mono)
        draw.text((cx + cw - 65, ey + 6), "[0.04s]", fill=MUTED_TEXT, font=f_mono)
        ey += 50
        
    # Step 2: Proposed Diff
    if step >= 2:
        draw.rectangle([(cx + 15, ey), (cx + cw - 15, ey + 115)], fill=(14, 20, 34), outline=BORDER_COL)
        draw.rectangle([(cx + 15, ey), (cx + cw - 15, ey + 24)], fill=(25, 34, 55))
        draw.text((cx + 25, ey + 4), "[CHANGE]  Cryptographic Proposal  |  SHA-256: 7f8c92a1... (Verified)", fill=ACCENT_PURPLE, font=get_font(9, bold=True))
        
        diff_snippets = [
            ("--- a/calculator.py", MUTED_TEXT),
            ("+++ b/calculator.py", MUTED_TEXT),
            ("@@ -2,4 +2,4 @@ def add(a, b):", ACCENT_CYAN),
            ("-    return a - b  # Subtraction bug", ACCENT_RED),
            ("+    return a + b  # Correct addition", ACCENT_EMERALD),
        ]
        dy = ey + 30
        for dline, dcol in diff_snippets:
            if dcol == ACCENT_RED:
                draw.rectangle([(cx + 17, dy - 1), (cx + cw - 17, dy + 15)], fill=(80, 20, 25))
            elif dcol == ACCENT_EMERALD:
                draw.rectangle([(cx + 17, dy - 1), (cx + cw - 17, dy + 15)], fill=(15, 65, 35))
            draw.text((cx + 25, dy), dline, fill=dcol, font=f_mono)
            dy += 16
        ey += 125
        
    # Step 3: Test Verification
    if step >= 3:
        draw.rectangle([(cx + 15, ey), (cx + cw - 15, ey + 75)], fill=(16, 23, 38), outline=ACCENT_EMERALD if step >= 4 else BORDER_COL)
        draw.text((cx + 25, ey + 6), "[TEST]  Toolchain: pytest runner (Sandboxed)", fill=WHITE_TEXT, font=get_font(10, bold=True))
        
        if step == 3:
            draw.text((cx + 25, ey + 26), "Running `python -m pytest tests/` in safe workspace...", fill=ACCENT_CYAN, font=f_mono)
            bw = int((cw - 50) * progress)
            draw.rectangle([(cx + 25, ey + 50), (cx + 25 + bw, ey + 56)], fill=ACCENT_CYAN)
        elif step >= 4:
            draw.text((cx + 25, ey + 26), "tests/test_calculator.py::test_add PASSED [100%]", fill=ACCENT_EMERALD, font=f_mono)
            draw.text((cx + 25, ey + 48), "All 2 tests passed in 0.38s • Exit code 0 (JUnit XML verified)", fill=WHITE_TEXT, font=f_mono)
        ey += 85
        
    # Step 4: Final Success Card
    if step >= 4:
        draw.rectangle([(cx + 15, ey), (cx + cw - 15, ey + 44)], fill=(16, 55, 32), outline=ACCENT_EMERALD)
        draw.text((cx + 25, ey + 8), "✅ TASK COMPLETE [VERIFIED]  •  Session 7f8c committed to Git checkpoint", fill=WHITE_TEXT, font=f_ui_b)
        draw.text((cx + 25, ey + 26), "Zero human work altered • Single-file restore ready via `undo`", fill=ACCENT_EMERALD, font=get_font(9))
        
    # Bottom Composer
    draw.rectangle([(cx + 15, HEIGHT - 68), (cx + cw - 15, HEIGHT - 18)], fill=(20, 28, 45), outline=BORDER_LIGHT)
    draw.text((cx + 28, HEIGHT - 54), "Type next instruction or use 12 unified commands (status, plan, diff)...", fill=MUTED_TEXT, font=f_ui)
    draw.rectangle([(cx + cw - 130, HEIGHT - 60), (cx + cw - 25, HEIGHT - 26)], fill=ACCENT_CYAN)
    draw.text((cx + cw - 120, HEIGHT - 52), "⚡ Send (Auto)", fill=BG_DARK, font=get_font(10, bold=True))
    
    # -------------------------------------------------------------
    # COLUMN 3: Right Rail (Diff Inspector & Proofs) - Width: 280px
    # -------------------------------------------------------------
    rx = cx + cw + 1
    rw = WIDTH - rx
    draw.rectangle([(rx, 45), (WIDTH, HEIGHT)], fill=SIDEBAR_BG)
    
    draw.text((rx + 16, 58), "DIFF INSPECTOR (Changeset)", fill=MUTED_TEXT, font=get_font(9, bold=True))
    
    # Side-by-Side Diff Box
    draw.rectangle([(rx + 12, 75), (WIDTH - 12, 290)], fill=(14, 20, 32), outline=BORDER_COL)
    draw.rectangle([(rx + 12, 75), (WIDTH - 12, 100)], fill=(22, 31, 50))
    draw.text((rx + 20, 82), "📄 calculator.py  (+1, -1)", fill=ACCENT_CYAN, font=f_mono_b)
    
    r_diffs = [
        (" 1  def add(a, b):", GRAY_TEXT),
        ("-2      return a - b", ACCENT_RED),
        ("+2      return a + b", ACCENT_EMERALD),
        (" 3  def subtract(a, b):", GRAY_TEXT),
        (" 4      return a - b", GRAY_TEXT),
    ]
    rdy = 110
    for rline, rcol in r_diffs:
        if rcol == ACCENT_RED:
            draw.rectangle([(rx + 14, rdy - 1), (WIDTH - 14, rdy + 15)], fill=(70, 20, 25))
        elif rcol == ACCENT_EMERALD:
            draw.rectangle([(rx + 14, rdy - 1), (WIDTH - 14, rdy + 15)], fill=(15, 60, 30))
        draw.text((rx + 20, rdy), rline, fill=rcol, font=f_mono)
        rdy += 18
        
    # Single-Key Approvals or Proofs Card
    if step == 2:
        draw.rectangle([(rx + 12, 305), (WIDTH - 12, 420)], fill=(35, 25, 45), outline=ACCENT_PURPLE)
        draw.text((rx + 20, 316), "⚠️ HUMAN APPROVAL REQUIRED", fill=ACCENT_PURPLE, font=get_font(10, bold=True))
        draw.text((rx + 20, 338), "Risk Band: LOW (Safe edit in repo)", fill=GRAY_TEXT, font=f_ui)
        draw.text((rx + 20, 358), "Press single key or click below:", fill=MUTED_TEXT, font=get_font(9))
        
        # [y] [n] [d] buttons
        draw.rectangle([(rx + 20, 380), (rx + 95, 408)], fill=ACCENT_EMERALD)
        draw.text((rx + 28, 388), "[y] Approve", fill=WHITE_TEXT, font=get_font(10, bold=True))
        draw.rectangle([(rx + 105, 380), (rx + 175, 408)], fill=(50, 60, 80))
        draw.text((rx + 115, 388), "[n] Reject", fill=WHITE_TEXT, font=get_font(10, bold=True))
        draw.rectangle([(rx + 185, 380), (rx + 250, 408)], fill=(40, 50, 70))
        draw.text((rx + 195, 388), "[d] Diff", fill=WHITE_TEXT, font=get_font(10, bold=True))
    else:
        # Proofs & Quality Badge Card
        draw.rectangle([(rx + 12, 305), (WIDTH - 12, 435)], fill=(16, 25, 38), outline=ACCENT_EMERALD if step >= 4 else BORDER_COL)
        draw.text((rx + 20, 316), "VERIFICATION PROOFS", fill=ACCENT_EMERALD if step >= 4 else GRAY_TEXT, font=get_font(10, bold=True))
        draw.text((rx + 20, 338), f"Badge: {'[VERIFIED] ✅' if step >= 4 else '[INFERRED] ⏳'}", fill=WHITE_TEXT, font=f_ui_b)
        draw.text((rx + 20, 360), "Tests: 2 passed, 0 failed", fill=GRAY_TEXT, font=f_ui)
        draw.text((rx + 20, 382), "Hash: 7f8c92a1... (SHA-256)", fill=MUTED_TEXT, font=f_mono)
        draw.text((rx + 20, 404), "AST Integrity: Clean (No tampering)", fill=ACCENT_EMERALD, font=get_font(9))
        
    # Selective Rollback Button
    draw.rectangle([(rx + 12, HEIGHT - 55), (WIDTH - 12, HEIGHT - 20)], fill=(28, 36, 52), outline=BORDER_COL)
    draw.text((rx + 45, HEIGHT - 44), "↩ Selective Rollback (undo)", fill=WHITE_TEXT, font=f_ui_b)
    
    return img

def create_complex_frame(step, progress=0.0):
    """Generates an animation demonstrating autonomous multi-file refactoring and 3-round self-repair loop."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)
    
    stage = "PLANNING" if step <= 1 else ("REVIEWING" if step == 2 else ("FIXING" if step == 3 else "DONE"))
    draw_top_bar(draw, "AI CODE ENGINEER — Autonomous Self-Healing Repair Loop", stage=stage, model="Google Gemini 3.8 Flash")
    
    f_ui = get_font(11)
    f_ui_b = get_font(11, bold=True)
    f_mono = get_font(11, mono=True)
    f_mono_b = get_font(11, bold=True, mono=True)
    
    # Left Rail
    draw.rectangle([(0, 45), (220, HEIGHT)], fill=SIDEBAR_BG)
    draw.line([(220, 45), (220, HEIGHT)], fill=BORDER_COL, width=1)
    
    draw.text((14, 58), "ENTERPRISE WORKSPACE", fill=MUTED_TEXT, font=get_font(9, bold=True))
    draw.text((16, 78), "📁 spring-auth-service", fill=ACCENT_CYAN, font=f_ui_b)
    draw.text((22, 100), "📄 SecurityConfig.java", fill=WHITE_TEXT, font=f_mono)
    draw.text((22, 120), "📄 JwtAuthFilter.java", fill=WHITE_TEXT, font=f_mono)
    draw.text((22, 140), "📄 RedisTokenStore.java", fill=WHITE_TEXT, font=f_mono)
    draw.text((22, 160), "📄 pom.xml", fill=MUTED_TEXT, font=f_mono)
    
    draw.text((14, 200), "REPAIR GOVERNOR", fill=MUTED_TEXT, font=get_font(9, bold=True))
    draw.text((16, 220), "Round Ceiling: 3 Rounds", fill=ACCENT_AMBER, font=f_ui)
    draw.text((16, 240), "Stagnation Check: Active", fill=ACCENT_EMERALD, font=f_ui)
    draw.text((16, 260), "Toolchain: Maven (mvn test)", fill=GRAY_TEXT, font=f_ui)
    
    draw.rectangle([(10, 310), (210, 420)], fill=(18, 25, 42), outline=BORDER_COL)
    draw.text((16, 322), "AST CODE GRAPH", fill=ACCENT_PURPLE, font=get_font(9, bold=True))
    draw.text((16, 342), "Layers: 13 Architectural", fill=WHITE_TEXT, font=f_ui)
    draw.text((16, 362), "Blast Radius: 3 Files", fill=ACCENT_CYAN, font=f_ui)
    draw.text((16, 382), "Security Gate: Verified", fill=ACCENT_EMERALD, font=f_ui)
    
    # Main Content Area
    cx = 240
    cy = 60
    
    # 1. User Specification
    draw.rectangle([(cx, cy), (WIDTH - 24, cy + 54)], fill=(28, 38, 62), outline=BORDER_LIGHT)
    draw.text((cx + 14, cy + 8), "USER SPECIFICATION", fill=ACCENT_CYAN, font=get_font(10, bold=True))
    draw.text((cx + 14, cy + 26), "Implement Redis token blacklist & RBAC in SecurityConfig; verify with `mvn test`", fill=WHITE_TEXT, font=f_ui)
    cy += 68
    
    # 2. AST Indexing
    if step >= 1:
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 40)], fill=(16, 23, 38), outline=BORDER_COL)
        draw.text((cx + 14, cy + 10), "🗺️ AST Code Graph: Resolved 14 symbols across SecurityConfig, JwtAuthFilter, RedisTokenStore", fill=GRAY_TEXT, font=f_mono)
        draw.text((WIDTH - 90, cy + 10), "[0.07s]", fill=ACCENT_CYAN, font=f_mono)
        cy += 50
        
    # 3. Multi-file Diff Proposal
    if step >= 2:
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 120)], fill=(14, 20, 34), outline=BORDER_COL)
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 24)], fill=(25, 34, 55))
        draw.text((cx + 12, cy + 4), "✍️ MULTI-FILE PROPOSAL (3 Files)  |  SHA-256: e83b109c... (Verified)", fill=ACCENT_PURPLE, font=get_font(9, bold=True))
        
        diffs = [
            ("--- a/config/SecurityConfig.java", MUTED_TEXT),
            ("+++ b/config/SecurityConfig.java", MUTED_TEXT),
            ("@@ -34,3 +34,6 @@ public SecurityFilterChain filterChain(HttpSecurity http) {", ACCENT_CYAN),
            ("-    http.authorizeHttpRequests(auth -> auth.anyRequest().authenticated());", ACCENT_RED),
            ("+    http.authorizeHttpRequests(auth -> auth.requestMatchers(\"/api/admin/**\").hasRole(\"ADMIN\")", ACCENT_EMERALD),
            ("+        .anyRequest().authenticated()).addFilterBefore(jwtAuthFilter(), UsernamePasswordAuthFilter.class);", ACCENT_EMERALD),
        ]
        dy = cy + 30
        for l, col in diffs:
            if col == ACCENT_RED:
                draw.rectangle([(cx + 4, dy - 1), (WIDTH - 28, dy + 15)], fill=(75, 20, 25))
            elif col == ACCENT_EMERALD:
                draw.rectangle([(cx + 4, dy - 1), (WIDTH - 28, dy + 15)], fill=(15, 60, 30))
            draw.text((cx + 14, dy), l, fill=col, font=f_mono)
            dy += 16
        cy += 130
        
    # 4. Self-Healing Round 1 (Error Detected)
    if step == 3:
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 115)], fill=(35, 18, 22), outline=ACCENT_RED)
        draw.text((cx + 14, cy + 10), "⚙️ TEST RUN #1: `mvn test`", fill=WHITE_TEXT, font=get_font(10, bold=True))
        draw.text((cx + 14, cy + 32), "❌ [ERROR] NoSuchBeanDefinitionException: No qualifying bean of type 'RedisTemplate'", fill=ACCENT_RED, font=f_mono)
        draw.text((cx + 14, cy + 54), "🔄 AUTONOMOUS REPAIR TRIGGERED: Feeding compiler trace to model for auto-repair...", fill=ACCENT_AMBER, font=f_mono)
        
        bw = int((WIDTH - cx - 50) * progress)
        draw.rectangle([(cx + 14, cy + 85), (cx + 14 + bw, cy + 93)], fill=ACCENT_AMBER)
        
    # 5. Fix Applied & All Green
    if step >= 4:
        draw.rectangle([(cx, cy), (WIDTH - 24, cy + 115)], fill=(15, 45, 28), outline=ACCENT_EMERALD)
        draw.text((cx + 14, cy + 10), "🔄 AUTONOMOUS REPAIR ROUND 2: Configured missing `@Bean RedisTemplate`", fill=ACCENT_CYAN, font=get_font(10, bold=True))
        draw.text((cx + 14, cy + 32), "⚙️ TEST RUN #2: `mvn test`", fill=WHITE_TEXT, font=f_mono)
        draw.text((cx + 14, cy + 54), "[INFO] Tests run: 18, Failures: 0, Errors: 0, Skipped: 0", fill=ACCENT_EMERALD, font=f_mono)
        draw.text((cx + 14, cy + 74), "[INFO] BUILD SUCCESS (JUnit XML evidence verified in 1.2s)", fill=WHITE_TEXT, font=f_mono)
        
        # Bottom Checkpoint Card
        draw.rectangle([(cx, HEIGHT - 55), (WIDTH - 24, HEIGHT - 18)], fill=(18, 55, 32), outline=ACCENT_EMERALD)
        draw.text((cx + 16, HEIGHT - 44), "✅ SELF-HEALING COMPLETE: Git checkpoint session-8f2a created • 18/18 tests green [VERIFIED]", fill=WHITE_TEXT, font=f_ui_b)

    return img

def main():
    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    
    print("[*] Generating Demo 1: Desktop WebApp Walkthrough (demo-webapp.gif & demo.gif)...")
    webapp_frames = []
    
    # Frame sequence for smooth visual story
    # Step 0: Initial prompt intake (10 frames)
    for _ in range(10):
        webapp_frames.append(create_webapp_frame(step=0))
    # Step 1: AST Code Graph Parsing (8 frames)
    for _ in range(8):
        webapp_frames.append(create_webapp_frame(step=1))
    # Step 2: Proposing Diff & Approval Required (14 frames)
    for _ in range(14):
        webapp_frames.append(create_webapp_frame(step=2))
    # Step 3: Running Test Suite with Progress Bar (12 frames)
    for i in range(12):
        webapp_frames.append(create_webapp_frame(step=3, progress=(i + 1) / 12))
    # Step 4: Verification Green State & Proofs (24 frames)
    for _ in range(24):
        webapp_frames.append(create_webapp_frame(step=4))
        
    p_webapp = assets_dir / "demo-webapp.gif"
    webapp_frames[0].save(
        p_webapp,
        save_all=True,
        append_images=webapp_frames[1:],
        duration=130,
        loop=0,
        optimize=True
    )
    print(f"[OK] Generated: {p_webapp} ({len(webapp_frames)} frames)")
    
    # Also save as assets/demo.gif
    p_demo = assets_dir / "demo.gif"
    webapp_frames[0].save(
        p_demo,
        save_all=True,
        append_images=webapp_frames[1:],
        duration=130,
        loop=0,
        optimize=True
    )
    print(f"[OK] Generated: {p_demo} ({len(webapp_frames)} frames)")

    print("\n[*] Generating Demo 2: Complex Multi-File & Self-Healing Loop (demo-complex.gif)...")
    complex_frames = []
    for _ in range(8):
        complex_frames.append(create_complex_frame(step=0))
    for _ in range(8):
        complex_frames.append(create_complex_frame(step=1))
    for _ in range(14):
        complex_frames.append(create_complex_frame(step=2))
    for i in range(14):
        complex_frames.append(create_complex_frame(step=3, progress=(i + 1) / 14))
    for _ in range(26):
        complex_frames.append(create_complex_frame(step=4))
        
    p_complex = assets_dir / "demo-complex.gif"
    complex_frames[0].save(
        p_complex,
        save_all=True,
        append_images=complex_frames[1:],
        duration=130,
        loop=0,
        optimize=True
    )
    print(f"[OK] Generated: {p_complex} ({len(complex_frames)} frames)")

if __name__ == "__main__":
    main()
