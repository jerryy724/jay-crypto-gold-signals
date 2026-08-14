from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 900
BLACK = (8, 8, 8)
CHARCOAL = (26, 26, 28)
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
    tw = bbox[2] - bbox[0]
    d.text(((w - tw) / 2, y), text, font=f, fill=fill)

def vertical_gradient(size, top_color, bottom_color):
    w, h = size
    base = Image.new("RGB", size, top_color)
    d = ImageDraw.Draw(base)
    for y in range(h):
        t = y / h
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
        d.line([(0, y), (w, y)], fill=(r, g, b))
    return base

def kente_strip(d, y, height, w=W):
    colors = [GOLD, RED, GREEN, (20, 20, 20)]
    block_w = 40
    i = 0
    x = 60
    while x < w - 60:
        c = colors[i % len(colors)]
        d.rectangle([x, y, x + block_w - 6, y + height], fill=c)
        x += block_w
        i += 1

def make_signal_card(direction, label, filename):
    accent = GREEN if direction == "BUY" else RED
    img = vertical_gradient((W, H), CHARCOAL, BLACK)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([14, 14, W - 14, H - 14], radius=28, outline=GOLD, width=3)
    d.rounded_rectangle([26, 26, W - 26, H - 26], radius=24, outline=accent, width=6)
    center_text(d, 60, f"★  {label}  ★", font(38), WHITE)
    d.line([(W / 2 - 160, 118), (W / 2 + 160, 118)], fill=GOLD, width=2)
    arrow = "▲" if direction == "BUY" else "▼"
    af, df = font(160), font(140)
    abbox = d.textbbox((0, 0), arrow, font=af)
    dbbox = d.textbbox((0, 0), direction, font=df)
    aw, dw = abbox[2] - abbox[0], dbbox[2] - dbbox[0]
    gap = 40
    total_w = aw + gap + dw
    start_x = (W - total_w) / 2
    ay = H / 2 - (abbox[3] - abbox[1]) / 2 - 30
    dy = H / 2 - (dbbox[3] - dbbox[1]) / 2 - 30
    d.text((start_x, ay), arrow, font=af, fill=accent)
    d.text((start_x + aw + gap, dy), direction, font=df, fill=accent)
    kente_strip(d, H - 160, 14)
    center_text(d, H - 120, "⚡ JAY GOLD MASTER ⚡", font(32), GOLD)
    img.save(filename)

def make_update_card(title, message, filename):
    img = vertical_gradient((W, H), CHARCOAL, BLACK)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([14, 14, W - 14, H - 14], radius=28, outline=GOLD, width=3)
    d.rounded_rectangle([26, 26, W - 26, H - 26], radius=24, outline=YELLOW, width=6)
    center_text(d, 60, "JAY GOLD MASTER", font(34), GOLD)
    center_text(d, 260, title, font(90), YELLOW)
    y = 420
    for line in message.split("\n"):
        center_text(d, y, line, font(30), WHITE)
        y += 44
    kente_strip(d, H - 160, 14)
    center_text(d, H - 120, "⚡ UPDATE ⚡", font(30), YELLOW)
    img.save(filename)
    
def make_brief_card(title, filename):
    img = vertical_gradient((W, H), CHARCOAL, BLACK)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([14, 14, W - 14, H - 14], radius=28, outline=GOLD, width=3)
    d.rounded_rectangle([26, 26, W - 26, H - 26], radius=24, outline=GOLD, width=6)
    center_text(d, 80, "★  JAY GOLD MASTER  ★", font(36), WHITE)
    d.line([(W / 2 - 160, 138), (W / 2 + 160, 138)], fill=GOLD, width=2)
    center_text(d, 340, "GOLD MARKET", font(70), GOLD)
    center_text(d, 430, "INTELLIGENCE", font(70), GOLD)
    center_text(d, 560, title, font(34), WHITE)
    kente_strip(d, H - 160, 14)
    center_text(d, H - 120, "⚡ JAY GOLD MASTER ⚡", font(32), GOLD)
    img.save(filename)
