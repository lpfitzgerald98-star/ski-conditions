from PIL import Image, ImageDraw
import math

BG = (11, 18, 32, 255)       # --bg
ACCENT = (86, 194, 255, 255) # --accent glacier blue

def draw_snowflake(d, cx, cy, r, color, width):
    for i in range(6):
        ang = math.radians(i * 60)
        x1, y1 = cx, cy
        x2, y2 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        d.line([(x1, y1), (x2, y2)], fill=color, width=width)
        # small branches
        for t in (0.45, 0.75):
            bx, by = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            for da in (-35, 35):
                bang = ang + math.radians(da)
                blen = r * 0.22
                d.line([(bx, by), (bx + blen * math.cos(bang), by + blen * math.sin(bang))],
                       fill=color, width=max(1, width - 1))

def draw_icon(size, padding_ratio):
    img = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    r = size / 2 * (1 - padding_ratio)
    draw_snowflake(d, cx, cy, r, ACCENT, max(2, size // 22))
    return img

draw_icon(192, 0.16).save("icon-192.png")
draw_icon(512, 0.16).save("icon-512.png")
draw_icon(180, 0.16).convert("RGB").save("apple-touch-icon.png")
draw_icon(192, 0.30).save("icon-192-maskable.png")
draw_icon(512, 0.30).save("icon-512-maskable.png")
print("done")
