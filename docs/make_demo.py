"""Render docs/demo.gif – an illustrative chat showing mediasense at work.

The video in the demo is fictional; the flow (link → progress → summary with timestamps) is the real one.
Run:  python docs/make_demo.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 880, 600
BG = (24, 24, 27)
PANEL = (32, 32, 36)
USER = (52, 74, 120)
TEXT = (232, 232, 236)
MUTED = (150, 150, 160)
ACCENT = (232, 140, 72)
GREEN = (110, 190, 120)
CHIP = (44, 44, 50)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSansMono.ttf" if mono else ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


F = font(17)
FB = font(17, bold=True)
FS = font(14)
FM = font(14, mono=True)
FT = font(15, bold=True)

PROMPT = "summarize this video: https://www.tiktok.com/@coffee.example/video/7350…"
STAGES = [
    ("downloading the video", None),
    ("transcribing", 18),
    ("transcribing", 47),
    ("transcribing", 83),
    ("picking key frames", None),
]
ANSWER = [
    ("b", "Perfect pour-over coffee in 60 seconds  (@coffee.example · 1:02)"),
    ("", ""),
    ("t", "0:04", "Grind 15 g of beans medium-fine, about like sea salt."),
    ("t", "0:15", "Rinse the paper filter with hot water and pour that water out."),
    ("t", "0:24", "Bloom: 30 g of water at 93 °C, then wait 30 seconds."),
    ("t", "0:41", "Pour the remaining 220 g in slow circles, centre outwards."),
    ("t", "0:55", "Tip: bitter coffee means grind coarser; sour means finer."),
    ("", ""),
    ("m", "On screen at 0:24 the scale reads 30 g, and at 0:55 she shows"),
    ("m", "two cups side by side to compare the grind sizes."),
]


def base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((16, 16, W - 16, H - 16), radius=16, fill=PANEL)
    for i, c in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        d.ellipse((34 + i * 20, 32, 46 + i * 20, 44), fill=c)
    d.text((W // 2, 38), "Claude + mediasense", font=FT, fill=MUTED, anchor="mm")
    d.line((16, 60, W - 16, 60), fill=(50, 50, 56))
    return im, d


def user_bubble(d: ImageDraw.ImageDraw, text: str, cursor: bool) -> int:
    shown = text + ("▍" if cursor else "")
    tw = d.textlength(PROMPT + "▍", font=F)
    x1 = W - 40
    x0 = x1 - tw - 32
    d.rounded_rectangle((x0, 80, x1, 124), radius=14, fill=USER)
    d.text((x0 + 16, 102), shown, font=F, fill=TEXT, anchor="lm")
    return 140


def tool_chip(d: ImageDraw.ImageDraw, y: int, stage: str, pct: int | None, done: bool, spin: int) -> int:
    label = "mediasense · understand_media"
    d.rounded_rectangle((40, y, 520, y + 58), radius=12, fill=CHIP)
    if done:
        d.text((58, y + 18), "✓", font=FB, fill=GREEN, anchor="lm")
    else:
        dots = "◐◓◑◒"[spin % 4]
        d.text((58, y + 18), dots, font=FB, fill=ACCENT, anchor="lm")
    d.text((84, y + 18), label, font=FB, fill=TEXT, anchor="lm")
    status = "done – transcript, 9 shots, key frames" if done else stage + (f"  {pct}%" if pct is not None else "…")
    d.text((84, y + 41), status, font=FM, fill=GREEN if done else MUTED, anchor="lm")
    if pct is not None and not done:
        d.rounded_rectangle((330, y + 36, 500, y + 46), radius=5, fill=(60, 60, 66))
        d.rounded_rectangle((330, y + 36, 330 + int(170 * pct / 100), y + 46), radius=5, fill=ACCENT)
    return y + 78


def answer(d: ImageDraw.ImageDraw, y: int, n_lines: int) -> None:
    for row in ANSWER[:n_lines]:
        kind = row[0]
        if kind == "b":
            d.text((44, y), row[1], font=FB, fill=TEXT)
        elif kind == "t":
            d.text((52, y), "•", font=F, fill=MUTED)
            d.text((70, y), row[1], font=FB, fill=ACCENT)
            d.text((120, y), row[2], font=F, fill=TEXT)
        elif kind == "m":
            d.text((44, y), row[1], font=FS, fill=MUTED)
        y += 30 if kind != "" else 14


def render(out: Path) -> Path:
    frames: list[Image.Image] = []
    durations: list[int] = []

    def add(im: Image.Image, ms: int) -> None:
        frames.append(im)
        durations.append(ms)

    # 1. empty window, then the user types the prompt
    im, d = base()
    add(im, 600)
    step = 3
    for i in range(0, len(PROMPT) + 1, step):
        im, d = base()
        user_bubble(d, PROMPT[:i], cursor=True)
        add(im, 45)
    im, d = base()
    user_bubble(d, PROMPT, cursor=False)
    add(im, 500)

    # 2. the tool runs, with progress
    spin = 0
    for stage, pct in STAGES:
        for _ in range(3):
            im, d = base()
            y = user_bubble(d, PROMPT, cursor=False)
            tool_chip(d, y, stage, pct, done=False, spin=spin)
            spin += 1
            add(im, 260)

    # 3. done, then the answer appears line by line
    for n in range(0, len(ANSWER) + 1):
        im, d = base()
        y = user_bubble(d, PROMPT, cursor=False)
        y = tool_chip(d, y, "", None, done=True, spin=0)
        answer(d, y, n)
        add(im, 650 if n and ANSWER[n - 1][0] == "t" else 300)
    durations[-1] = 4500  # hold the final frame

    pal = frames[-1].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    q[0].save(out, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True, disposal=1)
    return out


if __name__ == "__main__":
    p = render(Path(__file__).with_name("demo.gif"))
    print(p, f"{p.stat().st_size / 1024:.0f} KB")
