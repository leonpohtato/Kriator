# Kriator

Local MVP for turning an uploaded artwork into a beginner Krita guide pack with generated overlays, beginner instructions, and a live Krita coach docker.

## Demo In 5 Minutes

1. Start the local app:

```powershell
cd /d D:\data\krita-guide-agent\app
npm start
```

2. Open `http://localhost:8788`.
3. Upload a PNG/JPG/WEBP artwork, or a CLIP file if it contains a readable preview.
4. Click `Generate guide`.
5. Review the generated step guide, overlay images, palette, and Krita helper script.
6. For live drawing feedback, run `D:\data\krita-guide-agent\INSTALL_KRITA_LIVE_PLUGIN.cmd`, restart Krita, enable the plugin, then open `Settings > Dockers > Krita Guide Live Coach`.

The live coach can hide guide/reference layers during capture, map your current drawing to the reference even if it is shifted/scaled, show clickable section feedback, and display a side-by-side `Visual compare` preview for the selected region.

## Run

```powershell
cd /d D:\data\krita-guide-agent\app
npm start
```

Then open:

```text
http://localhost:8788
```

## Configure OpenAI

Copy `app\.env.example` to `app\.env` or `D:\data\krita-guide-agent\.env`, then set:

```text
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5.4-mini
```

If no key is set, the app still generates a local heuristic guide and marks it with a warning.

## Outputs

Generated projects are stored under:

```text
D:\data\krita-guide-agent\storage\artworks\<artwork-id>
```

Each project includes:

- `reference.png`
- `guide.json`
- `README.md`
- `palette.gpl`
- `overlays\step_*.png`
- `steps\step_*_card.png`
- `krita\guide_loader.py`

## Krita

Krita path defaults to:

```text
C:\Program Files\Krita (x64)\bin\krita.exe
```

Set `KRITA_PATH` in `.env` if your Krita install moves. The generated `krita\guide_loader.py` can be run from Krita's Scripter. If automatic file layers fail, manually import `reference.png` and the overlay PNGs.

## Live Krita Coach

For automatic overlay and feedback inside Krita, run:

```text
D:\data\krita-guide-agent\INSTALL_KRITA_LIVE_PLUGIN.cmd
```

Restart Krita, enable `Krita Guide Live Coach` in `Settings > Configure Krita > Python Plugin Manager`, restart Krita again, then open `Settings > Dockers > Krita Guide Live Coach`.

The docker captures the active document every few seconds, hides its own overlay before capture, and analyzes your whole drawing against the reference. It maps your marks to the reference even if your sketch is shifted or scaled on the canvas.

The segment list shows clickable comments for likely matching sections. Click a segment to lock focus there while you keep tweaking it; the comments and overlay keep updating for that same section. Press `Follow detected` to return to automatic section following.

The docker also has a `Visual compare` mode. For the selected segment it shows a side-by-side preview: the matching reference section on the left and your current drawing crop on the right. During lineart-only captures, flat-color, shadow, highlight, and detail steps are not counted as complete just because an outline crosses that region.

## Live Input Telemetry

The live coach records a beginner drawing session as JSONL under:

```text
D:\data\krita-guide-agent\storage\artworks\<artwork-id>\live_sessions\<session-id>.jsonl
```

Each record stores the current guide step, stage detection, feedback summary, visual-snapshot metrics, and app-wide tablet/mouse events that Krita/PyQt exposes during that interval. Pressure, tilt, and rotation are saved when the tablet event provides them. If Krita does not expose pressure for that event stream, the record explicitly shows `pressureAvailable: false` instead of guessing.

Events are also grouped into stroke summaries with duration, distance, bounding box, speed, and pressure stats. The record includes the active Krita layer name plus a beginner category such as `Rough Sketch`, `Lineart`, `Flat Colors`, `Shadows`, or `Highlights and Texture`. Multiple visible layers in the same category are assessed as one combined visible result, so layers like `My Shadows 1`, `My Shadows 2`, and `Wing Shadows` can all contribute to the same shadow-stage assessment.

Useful endpoints:

- `GET /api/artworks/<artwork-id>/live-sessions`
- `GET /api/artworks/<artwork-id>/live-sessions/<session-id>`
