"""Zoomed crops of chosen name cells, for checking the key by eye."""
import os, sys, json
sys.path.insert(0, ".")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.sqlite")
import django; django.setup()
import fitz, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
from workers import grid_detector, cell_ocr
targets = json.loads(sys.argv[1])  # [[page, "key name"], ...]
out = sys.argv[2]
font = ImageFont.truetype("C:/Windows/Fonts/tahoma.ttf", 22)
tiles = []
d = fitz.open("eval/sample.pdf")
for page_no in sorted({p for p, _ in targets}):
    key = json.load(open(f"eval/key/p{page_no:02d}.json", encoding="utf-8"))
    names = [r["name"] for r in key["rows"] if r["name"]]
    pm = d.load_page(page_no - 1).get_pixmap(dpi=200)
    im = cv2.cvtColor(np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n), cv2.COLOR_RGB2BGR)
    im, _ = grid_detector.orient(im); straight, grid = grid_detector.detect(im)
    ink, _ = cell_ocr._ink(straight)
    x0, x1 = int(grid.x_rules[1]) - 2, int(grid.x_rules[2]) + 2
    cells = [(int(a), int(b)) for a, b in zip(grid.y_rules, grid.y_rules[1:])
             if ink[int(a)+8:int(b)-8, x0+7:x1-7].sum() >= 255*20][1:]
    cells = cells[-len(names):]
    for p, name in targets:
        if p != page_no: continue
        a, b = cells[names.index(name)]
        crop = straight[a-10:b+6, x0:x1]
        crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        tile = Image.new("RGB", (crop.shape[1], crop.shape[0] + 34), "white")
        tile.paste(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)), (0, 34))
        ImageDraw.Draw(tile).text((4, 2), f"p{p} key: {name}", fill="red", font=font)
        tiles.append(tile)
W = max(t.width for t in tiles); H = sum(t.height for t in tiles)
sheet = Image.new("RGB", (W, H), "white"); y = 0
for t in tiles: sheet.paste(t, (0, y)); y += t.height
sheet.save(out); print(len(tiles), sheet.size)
