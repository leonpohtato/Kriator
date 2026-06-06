import io
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import textwrap
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

try:
    import cv2
except Exception:
    cv2 = None


def main():
    if len(sys.argv) < 2:
        fail("missing command")
    command = sys.argv[1]
    if command == "prepare":
        if len(sys.argv) != 5:
            fail("usage: prepare <artwork_dir> <original_path> <original_name>")
        result = prepare(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    elif command == "analyze":
        if len(sys.argv) != 3:
            fail("usage: analyze <artwork_dir>")
        result = analyze(Path(sys.argv[2]))
    elif command == "render":
        if len(sys.argv) != 4:
            fail("usage: render <artwork_dir> <guide_json>")
        result = render(Path(sys.argv[2]), Path(sys.argv[3]))
    elif command == "zip":
        if len(sys.argv) != 4:
            fail("usage: zip <artwork_dir> <zip_path>")
        result = zip_artwork(Path(sys.argv[2]), Path(sys.argv[3]))
    elif command == "live-feedback":
        if len(sys.argv) not in (5, 6):
            fail("usage: live-feedback <artworks_root> <snapshot_path> <project_id_or_empty> [focus_step]")
        focus_step = int(sys.argv[5]) if len(sys.argv) == 6 and str(sys.argv[5]).strip() else None
        result = live_feedback(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], focus_step)
    else:
        fail(f"unknown command: {command}")
    print(json.dumps(result, ensure_ascii=True))


def prepare(art_dir, original_path, original_name):
    art_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(original_name).suffix.lower()
    warnings = []
    if ext == ".clip":
        image, clip_info = extract_clip_preview(original_path, art_dir)
        warnings.extend(clip_info.get("warnings", []))
        source_type = "clip-preview"
    else:
        image = Image.open(original_path)
        source_type = "image"
        clip_info = {}

    image = normalize_reference(image)
    reference_path = art_dir / "reference.png"
    image.save(reference_path)
    thumbnail = image.copy()
    thumbnail.thumbnail((420, 420), Image.LANCZOS)
    thumbnail.save(art_dir / "thumbnail.png")

    info = {
        "sourceType": source_type,
        "sourceName": original_name,
        "width": image.width,
        "height": image.height,
        "reference": str(reference_path),
        "thumbnail": str(art_dir / "thumbnail.png"),
        "clip": clip_info,
        "warnings": warnings,
    }
    write_json(art_dir / "prepare.json", info)
    return info


def extract_clip_preview(original_path, art_dir):
    data = original_path.read_bytes()
    info = {"warnings": []}
    sqlite_offset = data.find(b"SQLite format 3\x00")
    if sqlite_offset >= 0:
        db_path = art_dir / "clip_internal.sqlite"
        db_path.write_bytes(data[sqlite_offset:])
        try:
            con = sqlite3.connect(str(db_path))
            row = con.execute(
                "select ImageWidth, ImageHeight, ImageData from CanvasPreview "
                "order by _PW_ID limit 1"
            ).fetchone()
            con.close()
            if row and row[2]:
                info.update({"sqliteOffset": sqlite_offset, "previewWidth": row[0], "previewHeight": row[1]})
                return Image.open(io.BytesIO(row[2])), info
        except Exception as exc:
            info["warnings"].append(f"CLIP SQLite preview extraction failed: {exc}")

    png = extract_first_png(data)
    if png:
        info["warnings"].append("Used raw PNG fallback because CLIP database preview was unavailable.")
        return Image.open(io.BytesIO(png)), info
    jpg = extract_first_jpeg(data)
    if jpg:
        info["warnings"].append("Used raw JPEG fallback because CLIP database preview was unavailable.")
        return Image.open(io.BytesIO(jpg)), info
    raise RuntimeError("Could not extract a preview from this .clip file. Export PNG/JPG from Clip Studio and upload that.")


def extract_first_png(data):
    sig = b"\x89PNG\r\n\x1a\n"
    start = data.find(sig)
    if start < 0:
        return None
    pos = start + len(sig)
    try:
        while pos + 8 <= len(data):
            length = int.from_bytes(data[pos:pos + 4], "big")
            chunk_type = data[pos + 4:pos + 8]
            pos += 8 + length + 4
            if chunk_type == b"IEND":
                return data[start:pos]
    except Exception:
        return None
    return None


def extract_first_jpeg(data):
    start = data.find(b"\xff\xd8\xff")
    if start < 0:
        return None
    end = data.find(b"\xff\xd9", start + 3)
    if end < 0:
        return None
    return data[start:end + 2]


def normalize_reference(image):
    image = ImageOps_exif_transpose(image)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    if image.mode == "RGBA":
        bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
        bg.alpha_composite(image)
        image = bg.convert("RGB")
    else:
        image = image.convert("RGB")
    max_side = max(image.size)
    if max_side > 2200:
        scale = 2200 / max_side
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
    return image


def ImageOps_exif_transpose(image):
    try:
        from PIL import ImageOps
        return ImageOps.exif_transpose(image)
    except Exception:
        return image


def analyze(art_dir):
    reference = Image.open(art_dir / "reference.png").convert("RGB")
    arr = np.array(reference)
    height, width = arr.shape[:2]
    bg = estimate_background(arr)
    diff = np.linalg.norm(arr.astype(np.int16) - np.array(bg, dtype=np.int16), axis=2)
    mask = diff > 24
    mask = clean_mask(mask)
    full_region = bbox_from_mask(mask, width, height, pad=18)
    components = component_regions(mask, width, height)
    palette = extract_palette(arr, mask)
    edge_density = compute_edge_density(arr, full_region)
    coverage = float(mask.sum()) / float(width * height)
    comp_count = len([c for c in components if c["area"] > 250])

    if coverage < 0.08 and comp_count <= 4 and edge_density < 0.04:
        complexity = "simple"
        step_count = 22
    elif coverage > 0.25 or comp_count > 10 or edge_density > 0.085:
        complexity = "complex"
        step_count = 48
    else:
        complexity = "medium"
        step_count = 34

    regions = derive_regions(full_region, width, height)
    result = {
        "width": width,
        "height": height,
        "background": rgb_to_hex(bg),
        "fullBbox": full_region,
        "coverage": round(coverage, 4),
        "edgeDensity": round(edge_density, 4),
        "componentCount": comp_count,
        "complexity": complexity,
        "suggestedStepCount": step_count,
        "palette": palette,
        "regions": regions,
        "components": components[:12],
    }
    write_json(art_dir / "analysis.json", result)
    save_edges(reference, art_dir / "analysis_edges.png")
    return result


def estimate_background(arr):
    h, w = arr.shape[:2]
    samples = []
    d = max(4, min(w, h) // 25)
    samples.extend(arr[:d, :d].reshape(-1, 3))
    samples.extend(arr[:d, w - d:w].reshape(-1, 3))
    samples.extend(arr[h - d:h, :d].reshape(-1, 3))
    samples.extend(arr[h - d:h, w - d:w].reshape(-1, 3))
    median = np.median(np.array(samples), axis=0)
    return tuple(int(v) for v in median)


def clean_mask(mask):
    if cv2 is None:
        return mask
    kernel = np.ones((3, 3), np.uint8)
    data = mask.astype(np.uint8) * 255
    data = cv2.morphologyEx(data, cv2.MORPH_OPEN, kernel, iterations=1)
    data = cv2.morphologyEx(data, cv2.MORPH_CLOSE, kernel, iterations=1)
    return data > 0


def bbox_from_mask(mask, width, height, pad=0):
    ys, xs = np.where(mask)
    if len(xs) == 0 or len(ys) == 0:
        return {"x": 0, "y": 0, "w": width, "h": height}
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(width - 1, int(xs.max()) + pad)
    y1 = min(height - 1, int(ys.max()) + pad)
    return {"x": x0, "y": y0, "w": max(1, x1 - x0 + 1), "h": max(1, y1 - y0 + 1)}


def component_regions(mask, width, height):
    if cv2 is None:
        return []
    data = (mask.astype(np.uint8) * 255)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(data, 8)
    comps = []
    for idx in range(1, num):
        x, y, w, h, area = [int(v) for v in stats[idx]]
        if area < 80:
            continue
        region = {
            "x": max(0, x - 12),
            "y": max(0, y - 12),
            "w": min(width - max(0, x - 12), w + 24),
            "h": min(height - max(0, y - 12), h + 24),
        }
        comps.append({"name": f"component_{idx}", "area": area, "region": region})
    comps.sort(key=lambda item: item["area"], reverse=True)
    return comps


def extract_palette(arr, mask):
    pixels = arr[mask]
    if pixels.size == 0:
        pixels = arr.reshape(-1, 3)
    stride = max(1, len(pixels) // 80000)
    pixels = pixels[::stride]
    counter = Counter()
    for r, g, b in pixels:
        key = (int(r) // 8 * 8, int(g) // 8 * 8, int(b) // 8 * 8)
        if key[0] > 245 and key[1] > 245 and key[2] > 245:
            continue
        counter[key] += 1
    palette = []
    for index, (rgb, count) in enumerate(counter.most_common(14), start=1):
        palette.append({
            "name": f"Color {index}",
            "hex": rgb_to_hex(rgb),
            "rgb": list(rgb),
            "count": int(count),
        })
    if not palette:
        palette.append({"name": "Line", "hex": "#050507", "rgb": [5, 5, 7], "count": 1})
    return palette


def compute_edge_density(arr, region):
    if cv2 is None:
        return 0.0
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    crop = arr[y:y + h, x:x + w]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 70, 150)
    return float((edges > 0).sum()) / float(max(1, w * h))


def derive_regions(full, width, height):
    x, y, w, h = full["x"], full["y"], full["w"], full["h"]
    third = max(1, h // 3)
    half_w = max(1, w // 2)
    center_w = max(1, int(w * 0.56))
    center_x = max(0, x + (w - center_w) // 2)
    return {
        "full": clamp_rect(x, y, w, h, width, height),
        "upper": clamp_rect(x, y, w, third + 20, width, height),
        "middle": clamp_rect(x, y + third - 20, w, third + 40, width, height),
        "lower": clamp_rect(x, y + 2 * third - 20, w, h - 2 * third + 20, width, height),
        "left": clamp_rect(x, y, half_w + 20, h, width, height),
        "right": clamp_rect(x + half_w - 20, y, w - half_w + 20, h, width, height),
        "center": clamp_rect(center_x, y, center_w, h, width, height),
    }


def clamp_rect(x, y, w, h, width, height):
    x = max(0, min(width - 1, int(x)))
    y = max(0, min(height - 1, int(y)))
    w = max(1, min(width - x, int(w)))
    h = max(1, min(height - y, int(h)))
    return {"x": x, "y": y, "w": w, "h": h}


def save_edges(image, output):
    if cv2 is None:
        return
    arr = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 70, 150)
    Image.fromarray(edges).save(output)


def render(art_dir, guide_path):
    guide = json.loads(guide_path.read_text(encoding="utf-8"))
    reference = Image.open(art_dir / "reference.png").convert("RGB")
    analysis = json.loads((art_dir / "analysis.json").read_text(encoding="utf-8")) if (art_dir / "analysis.json").exists() else {}

    for folder in ["overlays", "steps", "krita"]:
        (art_dir / folder).mkdir(exist_ok=True)

    render_palette(art_dir / "palette.gpl", guide, analysis)
    render_readme(art_dir / "README.md", guide, analysis)
    render_manifest(art_dir / "manifest.json", guide, analysis)
    render_krita_helper(art_dir, guide, reference.size)

    edge_image = get_edge_image(reference)
    for step in guide["steps"]:
        overlay = make_overlay(reference.size, edge_image, step)
        num = int(step["step"])
        overlay_path = art_dir / "overlays" / f"step_{num:03d}.png"
        overlay.save(overlay_path)
        card = make_step_card(reference, overlay, step, guide)
        card.save(art_dir / "steps" / f"step_{num:03d}_card.png")

    return {
        "stepCount": len(guide["steps"]),
        "overlays": str(art_dir / "overlays"),
        "steps": str(art_dir / "steps"),
        "kritaScript": str(art_dir / "krita" / "guide_loader.py"),
    }


def render_palette(output, guide, analysis):
    colors = []
    seen = set()
    for item in analysis.get("palette", []):
        add_color(colors, seen, item.get("hex"), item.get("name", "Sample"))
    for step in guide.get("steps", []):
        add_color(colors, seen, step.get("color"), step.get("title", "Step color"))
    lines = ["GIMP Palette", f"Name: {guide.get('title', 'Krita Guide Palette')}", "Columns: 6", "#"]
    for hex_color, name in colors:
        r, g, b = hex_to_rgb(hex_color)
        safe_name = re.sub(r"[^a-zA-Z0-9 _-]", "", name)[:40] or "Guide color"
        lines.append(f"{r:3d} {g:3d} {b:3d}\t{safe_name}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_color(colors, seen, hex_color, name):
    if not hex_color:
        return
    hex_color = normalize_hex(hex_color)
    if hex_color in seen:
        return
    seen.add(hex_color)
    colors.append((hex_color, name))


def render_readme(output, guide, analysis):
    lines = [
        f"# {guide.get('title', 'Krita Guide')}",
        "",
        guide.get("summary", ""),
        "",
        "## Setup",
        "",
        f"- Canvas: {analysis.get('width', guide['canvas']['w'])} x {analysis.get('height', guide['canvas']['h'])} px",
        "- Background: white paper layer, locked",
        "- Use the overlay PNGs as placement guides and the step cards as beginner checkpoints.",
        "- In Krita, keep reference/guide layers locked and draw on your own layers.",
        "",
        "## Style Notes",
        "",
    ]
    for note in guide.get("styleNotes", []):
        lines.append(f"- {note}")
    if guide.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in guide["warnings"]:
            lines.append(f"- {warning}")
    lines.extend(["", "## Steps", ""])
    for step in guide["steps"]:
        region = step["region"]
        lines.extend([
            f"### {step['step']:02d}. {step['title']}",
            "",
            f"- Layer: {step['layer']}",
            f"- Brush: {step['brush']}, {step['brushSizePx']} px, opacity {step['opacity']}%",
            f"- Color: {step['color']}",
            f"- Region: x={region['x']}, y={region['y']}, w={region['w']}, h={region['h']}",
            f"- Instruction: {step['instruction']}",
            f"- Checkpoint: {step['checkpoint']}",
            f"- Common mistake: {step['commonMistake']}",
            "",
        ])
    output.write_text("\n".join(lines), encoding="utf-8")


def render_manifest(output, guide, analysis):
    write_json(output, {
        "title": guide.get("title"),
        "complexity": guide.get("complexity"),
        "stepCount": len(guide.get("steps", [])),
        "canvas": guide.get("canvas"),
        "analysis": analysis,
        "artifacts": {
            "reference": "reference.png",
            "guide": "guide.json",
            "readme": "README.md",
            "palette": "palette.gpl",
            "overlays": "overlays/",
            "stepCards": "steps/",
            "kritaScript": "krita/guide_loader.py",
        },
    })


def render_krita_helper(art_dir, guide, size):
    script = art_dir / "krita" / "guide_loader.py"
    readme = art_dir / "krita" / "README_KRITA.txt"
    project_dir = str(art_dir).replace("\\", "\\\\")
    overlay_count = len(guide.get("steps", []))
    script.write_text(f'''# Generated by Krita Guide Agent.
# Open Krita, then run this in Tools > Scripts > Scripter.
# It creates a document and adds the reference plus overlay file layers when supported.

import os
from krita import Krita
from PyQt5.QtWidgets import QMessageBox

PROJECT_DIR = r"{project_dir}"
WIDTH = {int(size[0])}
HEIGHT = {int(size[1])}
OVERLAY_COUNT = {overlay_count}

app = Krita.instance()

def show(message):
    QMessageBox.information(None, "Krita Guide Agent", message)

def main():
    reference = os.path.join(PROJECT_DIR, "reference.png")
    if not os.path.exists(reference):
        show("reference.png was not found in the guide pack.")
        return

    doc = app.createDocument(WIDTH, HEIGHT, "Krita Guide Agent", "RGBA", "U8", "", 72.0)
    if app.activeWindow():
        app.activeWindow().addView(doc)
    root = doc.rootNode()

    try:
        ref_layer = doc.createFileLayer("00 Reference - lock this", reference, "None")
        ref_layer.setOpacity(120)
        root.addChildNode(ref_layer, None)
    except Exception as exc:
        show("Created document, but could not add file layers automatically. Open reference.png and overlays manually. Error: " + str(exc))
        return

    overlays_dir = os.path.join(PROJECT_DIR, "overlays")
    previous = ref_layer
    for index in range(OVERLAY_COUNT, 0, -1):
        overlay = os.path.join(overlays_dir, "step_" + str(index).zfill(3) + ".png")
        if not os.path.exists(overlay):
            continue
        try:
            layer = doc.createFileLayer("Guide overlay " + str(index).zfill(3), overlay, "None")
            layer.setOpacity(180)
            root.addChildNode(layer, previous)
            layer.setVisible(index == 1)
            previous = layer
        except Exception:
            pass
    doc.refreshProjection()
    show("Guide loaded. Toggle overlay layer visibility step by step. Palette file: " + os.path.join(PROJECT_DIR, "palette.gpl"))

main()
''', encoding="utf-8")
    readme.write_text(
        "Krita helper instructions\n"
        "1. Open Krita.\n"
        "2. Open Tools > Scripts > Scripter.\n"
        "3. Load and run guide_loader.py from this folder.\n"
        "4. If file layers fail, manually open reference.png and import overlays/step_*.png as guide layers.\n"
        "5. Import palette.gpl from Krita's palette docker or resource manager.\n",
        encoding="utf-8",
    )


def get_edge_image(reference):
    if cv2 is None:
        return reference.convert("L")
    arr = np.array(reference.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 70, 150)
    return Image.fromarray(edges, mode="L")


def make_overlay(size, edge_image, step):
    width, height = size
    overlay = Image.new("RGBA", size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    region = step["region"]
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    color = hex_to_rgb(step.get("color", "#0B7DFF"))
    fill = color + (42,)
    border = color + (220,)
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=fill, outline=border, width=max(2, min(width, height) // 250))

    edge_crop = edge_image.crop((x, y, x + w, y + h))
    colored_edges = Image.new("RGBA", (w, h), color + (0,))
    colored_edges.putalpha(edge_crop.point(lambda p: 210 if p > 0 else 0))
    overlay.alpha_composite(colored_edges, (x, y))

    label_font = load_font(22, bold=True)
    title_font = load_font(18, bold=False)
    number = str(step["step"])
    label_x = max(8, min(width - 90, x + 8))
    label_y = max(8, min(height - 70, y + 8))
    draw.rounded_rectangle([label_x, label_y, label_x + 64, label_y + 42], radius=8, fill=(255, 255, 255, 230), outline=border, width=2)
    draw.text((label_x + 10, label_y + 8), number, font=label_font, fill=(20, 24, 28, 255))
    title = truncate(step.get("title", ""), 34)
    title_w = draw.textlength(title, font=title_font)
    draw.rounded_rectangle([label_x + 72, label_y, label_x + 86 + title_w, label_y + 42], radius=8, fill=(255, 255, 255, 220), outline=border, width=2)
    draw.text((label_x + 80, label_y + 10), title, font=title_font, fill=(20, 24, 28, 255))

    cx, cy = x + w // 2, y + h // 2
    draw.line([label_x + 64, label_y + 42, cx, cy], fill=border, width=2)
    draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=border)
    return overlay


def make_step_card(reference, overlay, step, guide):
    card_w, card_h = 1500, 1000
    margin = 28
    image_area_w, image_area_h = 840, 900
    bg = Image.new("RGBA", (card_w, card_h), (249, 250, 248, 255))
    draw = ImageDraw.Draw(bg)
    title_font = load_font(30, bold=True)
    section_font = load_font(18, bold=True)
    body_font = load_font(18)
    small_font = load_font(15)

    draw.text((margin, 18), f"Step {step['step']:02d}: {step['title']}", font=title_font, fill=(18, 24, 28))

    ref = reference.copy()
    ref.thumbnail((image_area_w, image_area_h), Image.LANCZOS)
    scale = ref.width / reference.width
    faded = Image.blend(Image.new("RGB", ref.size, (255, 255, 255)), ref, 0.42)
    ov = overlay.resize(ref.size, Image.LANCZOS)
    panel_x = margin
    panel_y = 78
    bg.paste(faded.convert("RGBA"), (panel_x, panel_y))
    bg.alpha_composite(ov, (panel_x, panel_y))
    draw.rectangle([panel_x, panel_y, panel_x + ref.width, panel_y + ref.height], outline=(80, 90, 96), width=2)

    text_x = 900
    y = 82
    color = normalize_hex(step.get("color", "#050507"))
    draw_label(draw, text_x, y, "Layer", step["layer"], section_font, body_font)
    y += 66
    draw_label(draw, text_x, y, "Brush", f"{step['brush']} / {step['brushSizePx']} px / {step['opacity']}% opacity", section_font, body_font)
    y += 72
    r, g, b = hex_to_rgb(color)
    draw.text((text_x, y), "Color", font=section_font, fill=(40, 46, 52))
    draw.rounded_rectangle([text_x, y + 26, text_x + 58, y + 62], radius=6, fill=(r, g, b), outline=(40, 46, 52), width=1)
    draw.text((text_x + 72, y + 33), color, font=body_font, fill=(40, 46, 52))
    y += 96
    y = draw_wrapped_block(draw, text_x, y, "Do", step["instruction"], section_font, body_font, width=44)
    y = draw_wrapped_block(draw, text_x, y + 18, "Checkpoint", step["checkpoint"], section_font, body_font, width=44)
    y = draw_wrapped_block(draw, text_x, y + 18, "Common mistake", step["commonMistake"], section_font, body_font, width=44)
    region = step["region"]
    region_text = f"x {region['x']}, y {region['y']}, w {region['w']}, h {region['h']}"
    draw.text((text_x, card_h - 90), f"Canvas region: {region_text}", font=small_font, fill=(85, 92, 100))
    draw.text((text_x, card_h - 62), "Use the transparent overlay PNG in Krita as a guide layer.", font=small_font, fill=(85, 92, 100))
    return bg


def draw_label(draw, x, y, label, value, label_font, body_font):
    draw.text((x, y), label, font=label_font, fill=(40, 46, 52))
    draw.text((x, y + 26), value, font=body_font, fill=(20, 24, 28))


def draw_wrapped_block(draw, x, y, label, text, label_font, body_font, width=46):
    draw.text((x, y), label, font=label_font, fill=(40, 46, 52))
    y += 28
    for line in wrap_text(text, width):
        draw.text((x, y), line, font=body_font, fill=(20, 24, 28))
        y += 25
    return y


def wrap_text(text, width):
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    return lines


def load_font(size, bold=False):
    names = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\seguisb.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def truncate(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "."


def zip_artwork(art_dir, zip_path):
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(art_dir):
            for name in files:
                path = Path(root) / name
                if path == zip_path or path.suffix.lower() == ".zip":
                    continue
                archive.write(path, path.relative_to(art_dir))
    return {"zip": str(zip_path), "bytes": zip_path.stat().st_size}


def live_feedback(artworks_root, snapshot_path, project_id, focus_step=None):
    project = find_live_project(artworks_root, project_id)
    if not project:
        return {
            "status": "no_project",
            "message": "No ready guide project found. Generate a guide in the web app first.",
        }
    guide_path = project / "guide.json"
    analysis_path = project / "analysis.json"
    reference_path = project / "reference.png"
    if not guide_path.exists() or not reference_path.exists():
        return {
            "status": "project_not_ready",
            "projectId": project.name,
            "message": "The selected project is missing guide.json or reference.png.",
        }

    guide = json.loads(guide_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else {}
    snapshot = flatten_for_live(Image.open(snapshot_path))
    reference = Image.open(reference_path).convert("RGB")
    if snapshot.size != reference.size:
        snapshot = snapshot.resize(reference.size, Image.LANCZOS)

    snap_arr = np.array(snapshot)
    ref_arr = np.array(reference)
    snap_bg = estimate_background(snap_arr)
    snap_mask = np.linalg.norm(snap_arr.astype(np.int16) - np.array(snap_bg, dtype=np.int16), axis=2) > 34
    ref_bg = tuple(hex_to_rgb(analysis.get("background", "#FFFFFF"))) if analysis.get("background") else estimate_background(ref_arr)
    ref_mask = np.linalg.norm(ref_arr.astype(np.int16) - np.array(ref_bg, dtype=np.int16), axis=2) > 24
    snap_mask = clean_mask(snap_mask)
    ref_mask = clean_mask(ref_mask)

    active_bbox = bbox_from_mask(snap_mask, reference.width, reference.height, pad=8)
    ref_bbox = analysis.get("fullBbox") or bbox_from_mask(ref_mask, reference.width, reference.height, pad=8)
    total_drawn = int(snap_mask.sum())
    if total_drawn < 80:
        step = guide["steps"][0]
        return build_live_response(project, guide, step, 0.0, active_bbox, "Start with the first guide step. Your canvas is still almost blank.", [], segments=[])

    mapped_user_mask = map_mask_to_reference(snap_mask, active_bbox, ref_bbox, reference.width, reference.height)
    mapped_active_bbox = bbox_from_mask(mapped_user_mask, reference.width, reference.height, pad=8)
    mapped_area_ratio = (mapped_active_bbox["w"] * mapped_active_bbox["h"]) / max(1, ref_bbox["w"] * ref_bbox["h"])
    live_overlay_dir = project / "live_overlays"
    live_overlay_dir.mkdir(exist_ok=True)
    scored = []
    for step in guide.get("steps", []):
        step_number = int(step.get("step", 1))
        if is_live_prep_step(step):
            continue
        region = clamp_rect_dict(step.get("region", guide.get("canvas", {})), reference.width, reference.height)
        x, y, w, h = region["x"], region["y"], region["w"], region["h"]
        snap_region = mapped_user_mask[y:y + h, x:x + w]
        ref_region = ref_mask[y:y + h, x:x + w]
        drawn_in_region = int(snap_region.sum())
        ref_in_region = int(ref_region.sum())
        density = drawn_in_region / max(1, w * h)
        progress = drawn_in_region / max(1, ref_in_region)
        overlap = int(np.logical_and(snap_region, ref_region).sum()) / max(1, drawn_in_region)
        region_weight = min(1.0, ref_in_region / max(1, int(ref_mask.sum()) * 0.04))
        # Map the whole drawing into reference space so position/scale on canvas does not dominate.
        score = density * 2.2 + min(progress, 1.4) * 0.75 + overlap * 0.55 + region_weight * 0.2
        if is_focused_detail_step(step):
            score -= 0.12
        if step_number > 18:
            score -= min(0.25, (step_number - 18) * 0.008)
        region_area_ratio = (w * h) / max(1, reference.width * reference.height)
        title = str(step.get("title", "")).lower()
        if mapped_area_ratio > 0.35 and region_area_ratio < 0.08:
            score -= 0.8
        if mapped_area_ratio > 0.35 and is_focused_detail_step(step):
            score -= 0.35
        if mapped_area_ratio > 0.35 and ("clean outer line" in title or "big silhouette" in title):
            score += 0.38
        if focus_step == step_number:
            score += 0.65
        scored.append((score, progress, density, overlap, step, region, drawn_in_region, ref_in_region))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        step = first_drawing_step(guide)
        return build_live_response(project, guide, step, 0.0, active_bbox, "Start drawing on a paint layer. The coach will map your marks to the reference.", [], segments=[])

    segments = build_live_segments(project, guide, scored, focus_step, mapped_active_bbox, active_bbox, ref_bbox, reference.size, live_overlay_dir)

    best = None
    if focus_step is not None:
        best = next((item for item in scored if int(item[4].get("step", 1)) == focus_step), None)
    if best is None:
        best = scored[0]
    score, progress, density, overlap, step, region, drawn, ref_count = best
    comments = make_live_comments(step, region, mapped_active_bbox, progress, density, overlap, drawn, ref_count)
    next_step = suggest_next_step(guide, int(step.get("step", 1)), progress)
    live_overlay = render_transformed_live_overlay(project, live_overlay_dir, int(step.get("step", 1)), ref_bbox, active_bbox, reference.size)
    return build_live_response(
        project,
        guide,
        step,
        progress,
        active_bbox,
        comments[0],
        comments,
        next_step=next_step,
        score=score,
        live_overlay_path=live_overlay,
        segments=segments,
        alignment={"referenceBbox": ref_bbox, "drawingBbox": active_bbox, "mappedDrawingBbox": mapped_active_bbox},
    )


def build_live_segments(project, guide, scored, focus_step, mapped_active_bbox, active_bbox, ref_bbox, reference_size, live_overlay_dir):
    segments = []
    seen_detail_regions = set()
    broad_count = 0
    max_segments = 18

    def add_item(item, force=False):
        nonlocal broad_count
        if len(segments) >= max_segments and not force:
            return False
        score, progress, density, overlap, step, region, drawn, ref_count = item
        step_number = int(step.get("step", 1))
        if drawn < 25 and ref_count < 80 and len(segments) >= 6 and not force:
            return False
        area_ratio = (region["w"] * region["h"]) / max(1, reference_size[0] * reference_size[1])
        if area_ratio > 0.35 and broad_count >= 4 and not force:
            return False
        if is_focused_detail_step(step):
            key = region_signature(region)
            if key in seen_detail_regions and not force:
                return False
            seen_detail_regions.add(key)
        if area_ratio > 0.35:
            broad_count += 1
        comments = make_live_comments(step, region, mapped_active_bbox, progress, density, overlap, drawn, ref_count)
        live_overlay = render_transformed_live_overlay(project, live_overlay_dir, step_number, ref_bbox, active_bbox, reference_size)
        segments.append(segment_response(project, guide, step, progress, active_bbox, comments, score, live_overlay))
        return True

    if focus_step is not None:
        focused = next((item for item in scored if int(item[4].get("step", 1)) == focus_step), None)
        if focused:
            add_item(focused, force=True)

    for item in scored:
        if len(segments) >= max_segments:
            break
        if focus_step is not None and int(item[4].get("step", 1)) == focus_step:
            continue
        add_item(item)
    return segments


def first_drawing_step(guide):
    for step in guide.get("steps", []):
        if not is_live_prep_step(step):
            return step
    return guide.get("steps", [{}])[0]


def is_live_prep_step(step):
    title = str(step.get("title", "")).lower()
    return int(step.get("step", 1)) <= 2 or "canvas setup" in title or "place the reference" in title


def is_focused_detail_step(step):
    return str(step.get("title", "")).lower().startswith("focused detail pass")


def region_signature(region):
    return (
        round(region.get("x", 0) / 18),
        round(region.get("y", 0) / 18),
        round(region.get("w", 0) / 18),
        round(region.get("h", 0) / 18),
    )


def flatten_for_live(image):
    image = ImageOps_exif_transpose(image)
    if image.mode == "RGBA":
        # Krita exports transparent canvas pixels with alpha 0. Composite over white
        # so black drawing strokes do not disappear into a black transparent RGB value.
        bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
        bg.alpha_composite(image)
        return bg.convert("RGB")
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def map_mask_to_reference(mask, active_bbox, ref_bbox, width, height):
    mapped = np.zeros((height, width), dtype=bool)
    if int(mask.sum()) <= 0:
        return mapped
    ax, ay, aw, ah = active_bbox["x"], active_bbox["y"], active_bbox["w"], active_bbox["h"]
    rx, ry, rw, rh = ref_bbox["x"], ref_bbox["y"], ref_bbox["w"], ref_bbox["h"]
    crop = mask[ay:ay + ah, ax:ax + aw]
    if crop.size == 0:
        return mapped
    crop_img = Image.fromarray((crop.astype(np.uint8) * 255), mode="L").resize((rw, rh), Image.NEAREST)
    crop_arr = np.array(crop_img) > 0
    mapped[ry:ry + rh, rx:rx + rw] = crop_arr[: max(0, min(rh, height - ry)), : max(0, min(rw, width - rx))]
    return mapped


def render_transformed_live_overlay(project, live_overlay_dir, step_number, ref_bbox, active_bbox, size):
    source = project / "overlays" / f"step_{step_number:03d}.png"
    if not source.exists():
        return ""
    output = live_overlay_dir / f"live_step_{step_number:03d}.png"
    try:
        overlay = Image.open(source).convert("RGBA")
        rx, ry, rw, rh = ref_bbox["x"], ref_bbox["y"], ref_bbox["w"], ref_bbox["h"]
        ax, ay, aw, ah = active_bbox["x"], active_bbox["y"], active_bbox["w"], active_bbox["h"]
        crop = overlay.crop((rx, ry, rx + rw, ry + rh)).resize((max(1, aw), max(1, ah)), Image.LANCZOS)
        canvas = Image.new("RGBA", size, (255, 255, 255, 0))
        canvas.alpha_composite(crop, (ax, ay))
        canvas.save(output)
        return str(output)
    except Exception:
        return str(source)


def find_live_project(artworks_root, project_id):
    if project_id:
        candidate = artworks_root / project_id
        return candidate if candidate.exists() else None
    candidates = []
    if not artworks_root.exists():
        return None
    for child in artworks_root.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"
        guide_path = child / "guide.json"
        if not guide_path.exists():
            continue
        status = "ready"
        try:
            if meta_path.exists():
                status = json.loads(meta_path.read_text(encoding="utf-8")).get("status", "ready")
        except Exception:
            pass
        if status == "ready":
            candidates.append(child)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def clamp_rect_dict(region, width, height):
    return clamp_rect(region.get("x", 0), region.get("y", 0), region.get("w", width), region.get("h", height), width, height)


def make_live_comments(step, region, active_bbox, progress, density, overlap, drawn, ref_count):
    title = step.get("title", f"Step {step.get('step', '?')}")
    comments = []
    comments.append(f"Detected current focus: Step {step.get('step')}: {title}.")
    if progress < 0.12:
        comments.append("Very early in this area: block the biggest shape first before adding texture.")
    elif progress < 0.45:
        comments.append("Good start: keep building the main edges and proportions in the highlighted region.")
    elif progress < 0.85:
        comments.append("This region is partly built: compare the silhouette and major inner lines before moving on.")
    else:
        comments.append("This step looks substantially covered. You can refine edges or let the coach advance.")

    rx, ry = region["x"] + region["w"] / 2, region["y"] + region["h"] / 2
    ax, ay = active_bbox["x"] + active_bbox["w"] / 2, active_bbox["y"] + active_bbox["h"] / 2
    dx, dy = ax - rx, ay - ry
    active_area = active_bbox["w"] * active_bbox["h"]
    region_area = region["w"] * region["h"]
    if active_area <= region_area * 1.6:
        if abs(dx) > region["w"] * 0.35:
            comments.append("Your active marks are drifting horizontally from this step area; check placement against the overlay.")
        if abs(dy) > region["h"] * 0.35:
            comments.append("Your active marks are drifting vertically from this step area; zoom out and compare height.")
    if overlap < 0.18 and drawn > 250:
        comments.append("The marks do not overlap much with the reference structure yet; use the overlay to re-anchor the main contour.")
    if density > 0.23 and progress < 0.4:
        comments.append("This area is getting dense before the reference shape is covered. Use fewer, bigger strokes.")
    return comments


def suggest_next_step(guide, step_number, progress):
    steps = guide.get("steps", [])
    if progress < 0.85:
        return step_number
    return min(len(steps), step_number + 1)


def segment_response(project, guide, step, progress, active_bbox, comments, score, live_overlay_path):
    step_number = int(step.get("step", 1))
    overlay = project / "overlays" / f"step_{step_number:03d}.png"
    card = project / "steps" / f"step_{step_number:03d}_card.png"
    return {
        "step": step_number,
        "stepTitle": step.get("title", f"Step {step_number}"),
        "layer": step.get("layer", ""),
        "brush": step.get("brush", ""),
        "brushSizePx": step.get("brushSizePx", 0),
        "opacity": step.get("opacity", 100),
        "color": step.get("color", "#050507"),
        "region": step.get("region", guide.get("canvas", {})),
        "activeBbox": active_bbox,
        "progressPercent": round(max(0.0, min(progress * 100.0, 160.0)), 1),
        "score": round(float(score), 5),
        "message": comments[0] if comments else "",
        "comments": comments,
        "overlayPath": str(overlay) if overlay.exists() else "",
        "liveOverlayPath": live_overlay_path,
        "cardPath": str(card) if card.exists() else "",
        "instruction": step.get("instruction", ""),
        "checkpoint": step.get("checkpoint", ""),
        "commonMistake": step.get("commonMistake", ""),
    }


def build_live_response(project, guide, step, progress, active_bbox, message, comments, next_step=None, score=0.0, live_overlay_path="", segments=None, alignment=None):
    segment = segment_response(project, guide, step, progress, active_bbox, comments, score, live_overlay_path)
    segment.update({
        "status": "ok",
        "projectId": project.name,
        "title": guide.get("title", "Krita Guide"),
        "recommendedStep": next_step or segment["step"],
        "alignment": alignment or {},
        "segments": segments or [],
    })
    segment["message"] = message
    return segment


def rgb_to_hex(rgb):
    r, g, b = [max(0, min(255, int(v))) for v in rgb[:3]]
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_rgb(value):
    value = normalize_hex(value).lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def normalize_hex(value):
    text = str(value or "#050507").strip()
    short = re.fullmatch(r"#([0-9a-fA-F]{3})", text)
    if short:
        return "#" + "".join(ch * 2 for ch in short.group(1)).upper()
    full = re.fullmatch(r"#([0-9a-fA-F]{6})", text)
    return "#" + full.group(1).upper() if full else "#050507"


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
