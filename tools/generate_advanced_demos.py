"""Generates high-end animated GIFs for complex multi-file self-healing workflows and the desktop WebApp."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 940, 560
BG_DARK = (15, 23, 42)          # Slate 900
HEADER_BG = (30, 41, 59)        # Slate 800
SIDEBAR_BG = (13, 19, 33)       # Slate 950
ACCENT_CYAN = (56, 189, 248)     # Sky 400
ACCENT_PURPLE = (168, 85, 247)  # Purple 500
ACCENT_AMBER = (251, 191, 36)   # Amber 400
GREEN_TEXT = (74, 222, 128)     # Green 400
RED_TEXT = (248, 113, 113)      # Red 400
WHITE_TEXT = (248, 250, 252)
GRAY_TEXT = (148, 163, 184)
BORDER_COL = (51, 65, 85)

def get_font(size=13, bold=False):
    try:
        name = "consolab.ttf" if bold else "consola.ttf"
        return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
    except Exception:
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/arial{'bd' if bold else ''}.ttf", size)
        except Exception:
            return ImageFont.load_default()

def draw_window_header(draw, title="AI CODE ENGINEER - Autonomous Software Agent"):
    draw.rectangle([(0, 0), (WIDTH, HEIGHT)], fill=BG_DARK)
    draw.rectangle([(0, 0), (WIDTH, 42)], fill=HEADER_BG)
    draw.line([(0, 42), (WIDTH, 42)], fill=BORDER_COL, width=1)
    
    # Dots
    draw.ellipse([(16, 15), (28, 27)], fill=(239, 68, 68))
    draw.ellipse([(36, 15), (48, 27)], fill=(234, 179, 8))
    draw.ellipse([(56, 15), (68, 27)], fill=(34, 197, 94))
    
    draw.text((WIDTH // 2 - 140, 13), title, fill=WHITE_TEXT, font=get_font(13, bold=True))

def create_complex_frame(step, progress=0.0):
    """Demo 1: Complex Multi-file feature + Autonomous Self-Correction Loop."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_window_header(draw, "AI CODE ENGINEER — Autonomous Multi-File Refactor & Self-Healing")
    
    # Left Sidebar
    draw.rectangle([(0, 43), (220, HEIGHT)], fill=SIDEBAR_BG)
    draw.line([(220, 43), (220, HEIGHT)], fill=BORDER_COL, width=1)
    
    draw.text((16, 56), "ACTIVE WORKSPACE", fill=GRAY_TEXT, font=get_font(10, bold=True))
    draw.text((18, 76), "📁 spring-auth-service", fill=ACCENT_CYAN, font=get_font(12, bold=True))
    draw.text((24, 98), "📄 SecurityConfig.java", fill=WHITE_TEXT, font=get_font(11))
    draw.text((24, 116), "📄 JwtAuthFilter.java", fill=WHITE_TEXT, font=get_font(11))
    draw.text((24, 134), "📄 RedisTokenStore.java", fill=WHITE_TEXT, font=get_font(11))
    draw.text((24, 152), "📄 pom.xml", fill=GRAY_TEXT, font=get_font(11))
    
    draw.text((16, 185), "ACTIVE MODEL", fill=GRAY_TEXT, font=get_font(10, bold=True))
    draw.rectangle([(14, 202), (206, 234)], fill=(30, 41, 59), outline=ACCENT_PURPLE)
    draw.text((20, 212), "🧠 DeepSeek-R1 / Ollama", fill=WHITE_TEXT, font=get_font(11, bold=True))
    
    draw.text((16, 260), "AUTONOMOUS GATES", fill=GRAY_TEXT, font=get_font(10, bold=True))
    draw.text((18, 280), "🛡️ Zero-Trust Guard: ON", fill=GREEN_TEXT, font=get_font(11))
    draw.text((18, 300), "🔄 Self-Correction: 3 Runs", fill=ACCENT_AMBER, font=get_font(11))
    draw.text((18, 320), "📊 AST Symbol Graph: ON", fill=ACCENT_CYAN, font=get_font(11))
    
    cx = 236
    cy = 54
    f_code = get_font(12)
    f_chat = get_font(12)
    
    # User Request Box
    draw.rectangle([(cx, cy), (WIDTH - 20, cy + 54)], fill=(30, 41, 59), outline=BORDER_COL)
    draw.text((cx + 12, cy + 6), "USER SPECIFICATION", fill=ACCENT_CYAN, font=get_font(10, bold=True))
    req_text = "Implement Redis token blacklist & RBAC in SecurityConfig and verify with Maven test"
    draw.text((cx + 12, cy + 24), req_text, fill=WHITE_TEXT, font=f_chat)
    
    if step >= 1:
        # Step 1: AST Multi-File Indexing
        cy += 64
        draw.rectangle([(cx, cy), (WIDTH - 20, cy + 38)], fill=(17, 24, 39), outline=BORDER_COL)
        draw.text((cx + 12, cy + 10), "🗺️ AST Indexer: Parsed 14 symbols across 3 classes (`JwtService`, `SecurityConfig`, `AuthFilter`)", fill=GRAY_TEXT, font=f_code)
        draw.text((WIDTH - 90, cy + 10), "[0.08s]", fill=ACCENT_CYAN, font=f_code)
        
    if step >= 2:
        # Step 2: Multi-file Cryptographic Proposal
        cy += 48
        draw.rectangle([(cx, cy), (WIDTH - 20, cy + 150)], fill=(15, 23, 42), outline=BORDER_COL)
        draw.rectangle([(cx, cy), (WIDTH - 20, cy + 26)], fill=(30, 41, 59))
        draw.text((cx + 10, cy + 6), "✍️ MULTI-FILE DIFF PROPOSAL (3 Files)  |  SHA-256: e83b109c... [Verified]", fill=ACCENT_PURPLE, font=get_font(10, bold=True))
        
        diffs = [
            ("--- a/src/main/java/config/SecurityConfig.java", GRAY_TEXT),
            ("+++ b/src/main/java/config/SecurityConfig.java", GRAY_TEXT),
            ("@@ -34,3 +34,8 @@ public SecurityFilterChain filterChain(HttpSecurity http) {", ACCENT_CYAN),
            ("-    http.authorizeHttpRequests(auth -> auth.anyRequest().authenticated());", RED_TEXT),
            ("+    http.authorizeHttpRequests(auth -> auth.requestMatchers(\"/api/admin/**\").hasRole(\"ADMIN\")", GREEN_TEXT),
            ("+        .anyRequest().authenticated()).addFilterBefore(jwtAuthFilter(), UsernamePasswordAuthFilter.class);", GREEN_TEXT),
        ]
        dy = cy + 32
        for line, col in diffs:
            if col == RED_TEXT:
                draw.rectangle([(cx + 4, dy - 2), (WIDTH - 24, dy + 16)], fill=(127, 29, 29, 90))
            elif col == GREEN_TEXT:
                draw.rectangle([(cx + 4, dy - 2), (WIDTH - 24, dy + 16)], fill=(20, 83, 45, 90))
            draw.text((cx + 12, dy), line, fill=col, font=f_code)
            dy += 19

    if step == 3:
        # Step 3: Self-Healing Round 1 (Error Detected)
        cy += 160
        draw.rectangle([(cx, cy), (WIDTH - 20, cy + 110)], fill=(30, 20, 20), outline=(239, 68, 68))
        draw.text((cx + 12, cy + 10), "⚙️ TEST RUN #1: `mvn test`", fill=WHITE_TEXT, font=get_font(11, bold=True))
        draw.text((cx + 12, cy + 32), "❌ [ERROR] NoSuchBeanDefinitionException: No qualifying bean of type 'RedisTemplate'", fill=RED_TEXT, font=f_code)
        draw.text((cx + 12, cy + 54), "🔄 FEEDBACK LOOP TRIGGERED: Feeding compiler trace back to model for auto-repair...", fill=ACCENT_AMBER, font=f_code)
        # progress bar
        bw = int((WIDTH - cx - 50) * progress)
        draw.rectangle([(cx + 12, cy + 82), (cx + 12 + bw, cy + 90)], fill=ACCENT_AMBER)

    if step >= 4:
        # Step 4: Autonomous Fix Applied & Green
        cy += 160
        draw.rectangle([(cx, cy), (WIDTH - 20, cy + 140)], fill=(17, 24, 39), outline=(34, 197, 94))
        draw.text((cx + 12, cy + 10), "🔄 AUTONOMOUS REPAIR ROUND 2: Configured missing `@Bean RedisTemplate`", fill=ACCENT_CYAN, font=get_font(11, bold=True))
        draw.text((cx + 12, cy + 34), "⚙️ TEST RUN #2: `mvn test`", fill=WHITE_TEXT, font=f_code)
        draw.text((cx + 12, cy + 56), "[INFO] Tests run: 18, Failures: 0, Errors: 0, Skipped: 0", fill=GREEN_TEXT, font=f_code)
        draw.text((cx + 12, cy + 78), "[INFO] BUILD SUCCESS (JUnit XML evidence verified in 1.4s)", fill=GREEN_TEXT, font=f_code)
        
        # Bottom Checkpoint Card
        draw.rectangle([(cx, cy + 102), (WIDTH - 20, cy + 132)], fill=(20, 83, 45), outline=(34, 197, 94))
        draw.text((cx + 14, cy + 110), "✅ REPAIR COMPLETE: Git checkpoint created [session-8f2a] • All 18 tests verified", fill=WHITE_TEXT, font=get_font(11, bold=True))

    return img

def create_webapp_frame(step, progress=0.0):
    """Demo 2: Modern 3-Column Desktop WebApp UI Experience."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_window_header(draw, "AI CODE ENGINEER — Interactive Desktop WebApp UI")
    
    # Left Sidebar (Column 1)
    draw.rectangle([(0, 43), (200, HEIGHT)], fill=SIDEBAR_BG)
    draw.line([(200, 43), (200, HEIGHT)], fill=BORDER_COL, width=1)
    
    draw.text((14, 56), "PROJECTS & CHATS", fill=GRAY_TEXT, font=get_font(10, bold=True))
    draw.rectangle([(10, 74), (190, 104)], fill=(30, 41, 59), outline=ACCENT_CYAN)
    draw.text((16, 82), "📁 ecommerce (main ⚡)", fill=WHITE_TEXT, font=get_font(11, bold=True))
    
    draw.text((14, 120), "SAVED TASKS", fill=GRAY_TEXT, font=get_font(10, bold=True))
    draw.text((16, 138), "💬 1. Add Order Controller", fill=GRAY_TEXT, font=get_font(11))
    draw.text((16, 158), "💬 2. Implement Payment Gateway", fill=ACCENT_CYAN, font=get_font(11))
    
    draw.text((14, 200), "SETTINGS & MODEL", fill=GRAY_TEXT, font=get_font(10, bold=True))
    draw.rectangle([(10, 218), (190, 248)], fill=(17, 24, 39), outline=BORDER_COL)
    draw.text((16, 226), "🤖 Model: Groq / Llama-3.3", fill=ACCENT_PURPLE, font=get_font(10, bold=True))
    
    # Middle Area: Chat / Changes Stream (Column 2)
    mid_w = 460
    draw.rectangle([(201, 43), (201 + mid_w, HEIGHT)], fill=(15, 23, 42))
    draw.line([(201 + mid_w, 43), (201 + mid_w, HEIGHT)], fill=BORDER_COL, width=1)
    
    # Header navigation in middle area
    draw.rectangle([(201, 43), (201 + mid_w, 75)], fill=HEADER_BG)
    draw.text((220, 52), "[ Chat ]", fill=GRAY_TEXT, font=get_font(11, bold=True))
    draw.text((290, 52), "[ ⚡ Changes (Active) ]", fill=ACCENT_CYAN, font=get_font(11, bold=True))
    draw.text((450, 52), "[ Activity Logs ]", fill=GRAY_TEXT, font=get_font(11, bold=True))
    
    # Live Activity Shimmer
    draw.rectangle([(201, 75), (201 + mid_w, 98)], fill=(17, 24, 39))
    draw.text((215, 79), "⚡ Turn 2/12: Proposing changes for `PaymentService.java`...", fill=ACCENT_AMBER, font=get_font(10))
    
    # Chat / Step Rows
    my = 110
    draw.rectangle([(215, my), (201 + mid_w - 15, my + 44)], fill=(30, 41, 59))
    draw.text((225, my + 6), "User:", fill=ACCENT_CYAN, font=get_font(10, bold=True))
    draw.text((225, my + 22), "Integrate Stripe Checkout webhook with idempotency key", fill=WHITE_TEXT, font=get_font(11))
    
    my += 54
    draw.rectangle([(215, my), (201 + mid_w - 15, my + 30)], fill=(17, 24, 39))
    draw.text((225, my + 8), "📖 Read `PaymentService.java` & `OrderRepository.java`", fill=GRAY_TEXT, font=get_font(10))
    
    my += 38
    draw.rectangle([(215, my), (201 + mid_w - 15, my + 30)], fill=(17, 24, 39))
    draw.text((225, my + 8), "✍️ Proposed changes: 2 files modified (+48, -4)", fill=ACCENT_PURPLE, font=get_font(10))
    
    my += 38
    draw.rectangle([(215, my), (201 + mid_w - 15, my + 30)], fill=(17, 24, 39))
    draw.text((225, my + 8), "⚙️ Ran test suite: `mvn test` (JUnit XML verified)", fill=GREEN_TEXT, font=get_font(10))
    
    # Bottom composer in middle
    draw.rectangle([(215, HEIGHT - 85), (201 + mid_w - 15, HEIGHT - 20)], fill=(30, 41, 59), outline=ACCENT_CYAN)
    draw.text((225, HEIGHT - 75), "Describe your next change or question...", fill=GRAY_TEXT, font=get_font(11))
    draw.rectangle([(201 + mid_w - 105, HEIGHT - 52), (201 + mid_w - 25, HEIGHT - 26)], fill=ACCENT_CYAN)
    draw.text((201 + mid_w - 95, HEIGHT - 46), "⚡ Send (Auto)", fill=BG_DARK, font=get_font(10, bold=True))
    
    # Right Rail: Side-by-Side Diff & Checks Card (Column 3)
    rx = 201 + mid_w + 1
    rw = WIDTH - rx
    
    draw.rectangle([(rx, 43), (WIDTH, 75)], fill=HEADER_BG)
    draw.text((rx + 16, 52), "DIFF & INSPECTOR (Diff / Now / Was)", fill=WHITE_TEXT, font=get_font(11, bold=True))
    
    # Diff Box
    draw.rectangle([(rx + 10, 85), (WIDTH - 10, HEIGHT - 160)], fill=(17, 24, 39), outline=BORDER_COL)
    diff_lines = [
        ("PaymentService.java", ACCENT_CYAN),
        ("@@ -18,2 +18,9 @@ void handleWebhook()", GRAY_TEXT),
        ("+ String key = event.getId();", GREEN_TEXT),
        ("+ if (store.exists(key)) {", GREEN_TEXT),
        ("+     log.info(\"Duplicate event\");", GREEN_TEXT),
        ("+     return ResponseEntity.ok();", GREEN_TEXT),
        ("+ }", GREEN_TEXT),
        ("+ store.save(key);", GREEN_TEXT),
    ]
    rdy = 95
    for line, col in diff_lines:
        if col == GREEN_TEXT:
            draw.rectangle([(rx + 12, rdy - 1), (WIDTH - 12, rdy + 15)], fill=(20, 83, 45, 90))
        draw.text((rx + 16, rdy), line, fill=col, font=get_font(11))
        rdy += 18
        
    # Checks Card on Right
    draw.rectangle([(rx + 10, HEIGHT - 150), (WIDTH - 10, HEIGHT - 20)], fill=(20, 30, 20), outline=(34, 197, 94))
    draw.text((rx + 16, HEIGHT - 140), "CHECKS & PROOFS", fill=GREEN_TEXT, font=get_font(10, bold=True))
    draw.text((rx + 16, HEIGHT - 118), "✅ Maven Test: Passed (24 tests, 0 failed)", fill=WHITE_TEXT, font=get_font(11))
    draw.text((rx + 16, HEIGHT - 98), "📄 Report: target/surefire-reports/*.xml", fill=GRAY_TEXT, font=get_font(10))
    draw.text((rx + 16, HEIGHT - 78), "🔒 Hash: 9c8f2a1b (Locked & Applied)", fill=ACCENT_PURPLE, font=get_font(10))
    
    # Rollback Button
    draw.rectangle([(rx + 16, HEIGHT - 52), (rx + 120, HEIGHT - 28)], fill=(40, 50, 70), outline=BORDER_COL)
    draw.text((rx + 26, HEIGHT - 46), "↩ Roll Back", fill=WHITE_TEXT, font=get_font(10, bold=True))
    
    return img

def main():
    # 1. Generate Complex Multi-file & Self-healing GIF (assets/demo-complex.gif)
    complex_frames = []
    # Step 0: User Prompt (8 frames)
    for _ in range(8):
        complex_frames.append(create_complex_frame(step=0))
    # Step 1: AST Indexing (8 frames)
    for _ in range(8):
        complex_frames.append(create_complex_frame(step=1))
    # Step 2: Multi-file proposal (14 frames)
    for _ in range(14):
        complex_frames.append(create_complex_frame(step=2))
    # Step 3: Run 1 Error & Feedback Loop (16 frames)
    for i in range(16):
        complex_frames.append(create_complex_frame(step=3, progress=(i + 1) / 16))
    # Step 4: Autonomous Fix Round 2 & Green Checks (30 frames)
    for _ in range(30):
        complex_frames.append(create_complex_frame(step=4))
        
    p1 = Path(__file__).resolve().parents[1] / "assets" / "demo-complex.gif"
    complex_frames[0].save(p1, save_all=True, append_images=complex_frames[1:], duration=130, loop=0, optimize=True)
    print(f"Generated: {p1} ({len(complex_frames)} frames)")

    # 2. Generate Desktop WebApp Walkthrough GIF (assets/demo-webapp.gif)
    webapp_frames = []
    for _ in range(40):
        webapp_frames.append(create_webapp_frame(step=0))
    p2 = Path(__file__).resolve().parents[1] / "assets" / "demo-webapp.gif"
    webapp_frames[0].save(p2, save_all=True, append_images=webapp_frames[1:], duration=150, loop=0, optimize=True)
    print(f"Generated: {p2} ({len(webapp_frames)} frames)")

if __name__ == "__main__":
    main()
