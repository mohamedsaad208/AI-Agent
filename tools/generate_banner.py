"""Generate the official banner.svg with embedded program logo and cyberpunk theme."""
import base64
import io
from pathlib import Path
from PIL import Image

def generate():
    root = Path(__file__).resolve().parent.parent
    logo_path = root / "assets" / "logo.png"
    if not logo_path.exists():
        src_logo = root / "src" / "ai_code_engineer" / "webapp" / "static" / "favicon.png"
        logo_path.write_bytes(src_logo.read_bytes())

    im = Image.open(logo_path)
    im_thumb = im.resize((190, 190), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im_thumb.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 440" width="100%" height="100%">
  <defs>
    <!-- Background Gradients -->
    <linearGradient id="bg-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#070A0F"/>
      <stop offset="40%" stop-color="#0D111A"/>
      <stop offset="100%" stop-color="#150D2A"/>
    </linearGradient>

    <!-- Accent Gradients -->
    <linearGradient id="accent-grad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#00F2FE"/>
      <stop offset="45%" stop-color="#4FACFE"/>
      <stop offset="80%" stop-color="#8B5CF6"/>
      <stop offset="100%" stop-color="#EC4899"/>
    </linearGradient>

    <linearGradient id="logo-border" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00F2FE"/>
      <stop offset="50%" stop-color="#8B5CF6"/>
      <stop offset="100%" stop-color="#EC4899"/>
    </linearGradient>

    <!-- Grid Pattern -->
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1E293B" stroke-width="0.6" opacity="0.55"/>
    </pattern>

    <!-- Dot Pattern -->
    <pattern id="dots" width="20" height="20" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="0.8" fill="#334155" opacity="0.45"/>
    </pattern>

    <!-- Glow Filters -->
    <filter id="glow" x="-25%" y="-25%" width="150%" height="150%">
      <feGaussianBlur stdDeviation="10" result="blur" />
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>

    <filter id="soft-shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="8" stdDeviation="14" flood-color="#000000" flood-opacity="0.65"/>
    </filter>

    <radialGradient id="sphere-cyan" cx="20%" cy="30%" r="45%">
      <stop offset="0%" stop-color="#00F2FE" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="#00F2FE" stop-opacity="0"/>
    </radialGradient>

    <radialGradient id="sphere-purple" cx="80%" cy="35%" r="50%">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#8B5CF6" stop-opacity="0"/>
    </radialGradient>

    <radialGradient id="sphere-rose" cx="50%" cy="85%" r="40%">
      <stop offset="0%" stop-color="#EC4899" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="#EC4899" stop-opacity="0"/>
    </radialGradient>
  </defs>

  <!-- Background Layers -->
  <rect width="1200" height="440" fill="url(#bg-grad)"/>
  <rect width="1200" height="440" fill="url(#grid)"/>
  <rect width="1200" height="440" fill="url(#dots)"/>

  <!-- Ambient Glowing Orbs -->
  <circle cx="180" cy="140" r="200" fill="url(#sphere-cyan)"/>
  <circle cx="1020" cy="180" r="240" fill="url(#sphere-purple)"/>
  <circle cx="600" cy="380" r="180" fill="url(#sphere-rose)"/>

  <!-- Border Frame -->
  <rect x="1" y="1" width="1198" height="438" fill="none" stroke="#334155" stroke-width="1.5" rx="16" opacity="0.8"/>

  <!-- Outer Tech Accents -->
  <path d="M 20 40 L 20 20 L 40 20" fill="none" stroke="#00F2FE" stroke-width="2.5" opacity="0.85"/>
  <path d="M 1180 40 L 1180 20 L 1160 20" fill="none" stroke="#8B5CF6" stroke-width="2.5" opacity="0.85"/>
  <path d="M 20 400 L 20 420 L 40 420" fill="none" stroke="#8B5CF6" stroke-width="2.5" opacity="0.85"/>
  <path d="M 1180 400 L 1180 420 L 1160 420" fill="none" stroke="#EC4899" stroke-width="2.5" opacity="0.85"/>

  <!-- Top Decorative Tech Pill -->
  <g transform="translate(600, 32)" text-anchor="middle">
    <rect x="-165" y="-12" width="330" height="24" rx="12" fill="#101726" stroke="#1E293B" stroke-width="1"/>
    <circle cx="-140" cy="0" r="3.5" fill="#10B981" filter="url(#glow)"/>
    <text x="-124" y="4" font-family="'Segoe UI', Inter, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#94A3B8" letter-spacing="1.5" text-anchor="start">AUTONOMOUS SOFTWARE ENGINEERING AGENT</text>
  </g>

  <!-- Central Hero Section -->
  <g transform="translate(600, 168)" text-anchor="middle">
    <!-- Official Logo Container -->
    <g transform="translate(0, -68)" filter="url(#soft-shadow)">
      <!-- Glow backlight behind logo -->
      <rect x="-56" y="-56" width="112" height="112" rx="28" fill="none" stroke="url(#logo-border)" stroke-width="4" opacity="0.45" filter="url(#glow)"/>
      <!-- Main Logo Card -->
      <rect x="-50" y="-50" width="100" height="100" rx="24" fill="#0B0F19" stroke="url(#logo-border)" stroke-width="2.2"/>
      <!-- Inner subtle highlight -->
      <rect x="-46" y="-46" width="92" height="92" rx="20" fill="none" stroke="#ffffff" stroke-width="0.5" opacity="0.15"/>
      <!-- Embedded Logo Image -->
      <clipPath id="logo-clip">
        <rect x="-44" y="-44" width="88" height="88" rx="18"/>
      </clipPath>
      <image href="data:image/png;base64,{b64}" x="-44" y="-44" width="88" height="88" clip-path="url(#logo-clip)" preserveAspectRatio="xMidYMid slice"/>
    </g>

    <!-- Main Title -->
    <text y="46" font-family="'Segoe UI Variable Display', 'Segoe UI', Inter, -apple-system, sans-serif" font-size="50" font-weight="900" letter-spacing="2.5" fill="#F8FAFC">
      AI CODE <tspan fill="url(#accent-grad)">ENGINEER</tspan>
    </text>

    <!-- Subtitle / Core Motto -->
    <text y="82" font-family="'Segoe UI Variable Text', 'Segoe UI', Inter, -apple-system, sans-serif" font-size="16" font-weight="600" fill="#94A3B8" letter-spacing="3.5">
      AUTONOMOUS • ZERO-TRUST • ARCHITECTURE-AWARE • SELF-HEALING
    </text>
  </g>

  <!-- Capability Badges Row -->
  <g transform="translate(600, 375)" text-anchor="middle">
    <!-- Badge 1: Spring Boot Scanner -->
    <g transform="translate(-370, 0)">
      <rect x="-92" y="-18" width="184" height="36" rx="18" fill="#0E1422" stroke="#00F2FE" stroke-width="1.2" opacity="0.9"/>
      <circle cx="-68" cy="0" r="4" fill="#00F2FE"/>
      <text x="6" y="5" font-family="'Segoe UI', Inter, sans-serif" font-size="12" font-weight="600" fill="#E2E8F0" text-anchor="middle">13-Layer Repo Scanner</text>
    </g>

    <!-- Badge 2: Auto Test / Fix Loop -->
    <g transform="translate(-185, 0)">
      <rect x="-86" y="-18" width="172" height="36" rx="18" fill="#0E1422" stroke="#8B5CF6" stroke-width="1.2" opacity="0.9"/>
      <circle cx="-62" cy="0" r="4" fill="#8B5CF6"/>
      <text x="6" y="5" font-family="'Segoe UI', Inter, sans-serif" font-size="12" font-weight="600" fill="#E2E8F0" text-anchor="middle">Auto Test &amp; Fix Loop</text>
    </g>

    <!-- Badge 3: Deterministic State Machine -->
    <g transform="translate(0, 0)">
      <rect x="-92" y="-18" width="184" height="36" rx="18" fill="#0E1422" stroke="#10B981" stroke-width="1.2" opacity="0.9"/>
      <circle cx="-68" cy="0" r="4" fill="#10B981"/>
      <text x="6" y="5" font-family="'Segoe UI', Inter, sans-serif" font-size="12" font-weight="600" fill="#E2E8F0" text-anchor="middle">Typed State Machine</text>
    </g>

    <!-- Badge 4: Zero-Trust Security Sandbox -->
    <g transform="translate(185, 0)">
      <rect x="-86" y="-18" width="172" height="36" rx="18" fill="#0E1422" stroke="#F59E0B" stroke-width="1.2" opacity="0.9"/>
      <circle cx="-62" cy="0" r="4" fill="#F59E0B"/>
      <text x="6" y="5" font-family="'Segoe UI', Inter, sans-serif" font-size="12" font-weight="600" fill="#E2E8F0" text-anchor="middle">Zero-Trust Sandbox</text>
    </g>

    <!-- Badge 5: Arabic RTL & English -->
    <g transform="translate(370, 0)">
      <rect x="-92" y="-18" width="184" height="36" rx="18" fill="#0E1422" stroke="#EC4899" stroke-width="1.2" opacity="0.9"/>
      <circle cx="-68" cy="0" r="4" fill="#EC4899"/>
      <text x="6" y="5" font-family="'Segoe UI', Inter, sans-serif" font-size="12" font-weight="600" fill="#E2E8F0" text-anchor="middle">Arabic RTL &amp; English</text>
    </g>
  </g>
</svg>
"""
    banner_path = root / "assets" / "banner.svg"
    banner_path.write_text(svg, encoding="utf-8")
    print(f"Generated {banner_path} successfully ({len(svg)} bytes)")

if __name__ == "__main__":
    generate()
