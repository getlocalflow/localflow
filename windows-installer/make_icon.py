"""Generate the LocalFlow tray/installer icon (no binary assets in git).
Draws a rounded mic glyph; writes multi-size .ico."""
import sys
from PIL import Image, ImageDraw


def build(path):
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16, 16, 240, 240), radius=56, fill=(30, 30, 34, 255))
    d.rounded_rectangle((96, 52, 160, 156), radius=32, fill=(126, 200, 255, 255))
    d.arc((72, 96, 184, 196), start=0, end=180, fill=(232, 232, 232, 255), width=14)
    d.line((128, 196, 128, 224), fill=(232, 232, 232, 255), width=14)
    d.line((100, 224, 156, 224), fill=(232, 232, 232, 255), width=14)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "localflow.ico")
