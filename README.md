# Krita Guide Agent

Local MVP for turning an uploaded artwork into a beginner Krita guide pack.

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

The docker captures the active document every few seconds, hides its own overlay before capture, asks the local app which guide step matches the current drawing area, and replaces the live overlay layer automatically.
