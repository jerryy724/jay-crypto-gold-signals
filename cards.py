from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
BLACK = (10, 10, 10)
GOLD = (212, 175, 55)
WHITE = (240, 240, 240)
GREEN = (46, 204, 113)
RED = (231, 76, 60)
YELLOW = (241, 196, 15)
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def font(size):
    return ImageFont.truetype(BOLD, size)

def center_text(d, y, text, f, fill, w=W):
    bbox = d.textbbox((0, 0), text, font=f)
    d.text(((w - (bbox[2] - bbox[0])) / 2, y), text, font=f, fill=fill)

def make_signal_card(direction, label, filename):
    accent = GREEN if direction == "BUY" else RED
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16, 16, W-16, H-16], radius=34, outline=accent, width=8)
    d.rounded_rectangle([38, 38, W-38, H-38], radius=24, outline=(55, 55, 55), width=2)
    center_text(d, 110, f"★  {label}  ★", font(42), WHITE)
    arrow = "▲" if direction == "BUY" else "▼"
    center_text(d, 460, arrow, font(130), accent)
    center_text(d, 640, direction, font(150), accent)
    center_text(d, H-160, "⚡ JAY CRYPTGOLD SIGNALS ⚡", font(34), GOLD)
    img.save(filename)

def make_update_card(title, message, filename):
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16, 16, W-16, H-16], radius=34, outline=YELLOW, width=8)
    d.rounded_rectangle([38, 38, W-38, H-38], radius=24, outline=(55, 55, 55), width=2)
    center_text(d, 110, "JAY CRYPTGOLD SIGNALS", font(38), GOLD)
    center_text(d, 480, title, font(74), YELLOW)
    y = 640
    for line in message.split("\n"):
        center_text(d, y, line, font(32), WHITE)
        y += 48
    center_text(d, H-160, "⚡ UPDATE ⚡", font(34), YELLOW)
    img.save(filename)
