"""Render docs/social.png (1280x640): the GitHub social preview and the landing page's share image.

    python docs/make_social.py

Upload the result in GitHub → Settings → General → Social preview.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1280, 640
BG = (24, 24, 28)
FG = (240, 240, 242)
MUTED = (165, 165, 175)
ORANGE = (232, 140, 72)
CHIP = (44, 44, 50)

CANDIDATES = {
    "regular": ["DejaVuSans.ttf", "segoeui.ttf", "arial.ttf", "Helvetica.ttc"],
    "bold": ["DejaVuSans-Bold.ttf", "segoeuib.ttf", "arialbd.ttf", "Helvetica.ttc"],
    "mono": ["DejaVuSansMono.ttf", "consola.ttf", "cour.ttf", "Menlo.ttc"],
}
FONT_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("C:/Windows/Fonts"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
]


def font(size: int, kind: str = "regular") -> ImageFont.FreeTypeFont:
    for name in CANDIDATES[kind]:
        for folder in FONT_DIRS:
            if (folder / name).exists():
                return ImageFont.truetype(str(folder / name), size)
    return ImageFont.load_default(size)


def render(out: Path) -> Path:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    icon = Image.open(ROOT / "mcpb" / "icon.png").convert("RGBA").resize((300, 300), Image.LANCZOS)
    im.paste(icon, (90, 120), icon)

    x = 450
    d.text((x, 112), "mediasense", font=font(92, "bold"), fill=FG)
    d.text((x, 230), "Give Claude ears and eyes", font=font(44, "bold"), fill=ORANGE)
    body = font(30)
    d.text((x, 304), "Summarize, explain, transcribe and edit", font=body, fill=MUTED)
    d.text((x, 346), "TikTok, YouTube and Instagram videos.", font=body, fill=MUTED)
    d.text((x, 388), "Runs locally · MCP server for Claude.", font=body, fill=MUTED)

    mono = font(26, "mono")
    chips = ["pip install mediasense", "Claude Desktop", "Claude Code"]
    cx, cy = 90, 488
    for text in chips:
        w = d.textlength(text, font=mono)
        d.rounded_rectangle((cx, cy, cx + w + 40, cy + 54), radius=27, fill=CHIP)
        d.text((cx + 20, cy + 27), text, font=mono, fill=FG, anchor="lm")
        cx += w + 64

    im.save(out, optimize=True)
    return out


if __name__ == "__main__":
    print(render(Path(__file__).with_name("social.png")))
