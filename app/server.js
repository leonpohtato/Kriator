const http = require("http");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { spawn, spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
loadEnv(path.join(ROOT, ".env"));
loadEnv(path.join(__dirname, ".env"));

const PORT = Number(process.env.PORT || 8788);
const PYTHON = process.env.PYTHON || "python";
const OPENAI_MODEL = process.env.OPENAI_MODEL || "gpt-5.4-mini";
const KRITA_PATH = process.env.KRITA_PATH || "C:\\Program Files\\Krita (x64)\\bin\\krita.exe";
const KEEP_LATEST = Number(process.env.GUIDE_AGENT_KEEP_LATEST || 20);
const STORAGE_ROOT = path.join(ROOT, "storage");
const ARTWORKS_ROOT = path.join(STORAGE_ROOT, "artworks");
const TMP_ROOT = path.join(STORAGE_ROOT, "tmp");
const PUBLIC_ROOT = path.join(__dirname, "public");
const WORKER = path.join(__dirname, "scripts", "artifact_worker.py");
const MAX_UPLOAD_BYTES = 90 * 1024 * 1024;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".zip": "application/zip",
  ".py": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".gpl": "text/plain; charset=utf-8"
};

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

async function main() {
  await ensureDirs();
  const server = http.createServer((req, res) => {
    handle(req, res).catch((error) => {
      console.error(error);
      sendJson(res, 500, { ok: false, error: error.message || String(error) });
    });
  });
  server.listen(PORT, () => {
    console.log(`Krita Guide Agent running at http://localhost:${PORT}`);
    console.log(`Storage: ${STORAGE_ROOT}`);
  });
}

async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const method = req.method || "GET";

  if (method === "GET" && url.pathname === "/") {
    return serveStatic(res, path.join(PUBLIC_ROOT, "index.html"));
  }
  if (method === "GET" && url.pathname.startsWith("/assets/")) {
    return serveSafeFile(res, PUBLIC_ROOT, url.pathname.slice("/assets/".length));
  }
  if (method === "GET" && url.pathname.startsWith("/files/")) {
    const [id, ...parts] = url.pathname.slice("/files/".length).split("/");
    assertArtworkId(id);
    return serveSafeFile(res, artworkDir(id), decodeURIComponent(parts.join("/")));
  }

  if (method === "GET" && url.pathname === "/api/health") {
    return sendJson(res, 200, {
      ok: true,
      port: PORT,
      root: ROOT,
      storageRoot: STORAGE_ROOT,
      artworksRoot: ARTWORKS_ROOT,
      openaiConfigured: Boolean(process.env.OPENAI_API_KEY),
      model: OPENAI_MODEL,
      kritaPath: KRITA_PATH,
      keepLatest: KEEP_LATEST
    });
  }

  if (method === "GET" && url.pathname === "/api/artworks") {
    return sendJson(res, 200, { ok: true, artworks: await listArtworks() });
  }

  if (method === "GET" && url.pathname === "/api/live/latest-project") {
    const artworks = await listArtworks();
    const ready = artworks.find((artwork) => artwork.status === "ready") || artworks[0] || null;
    return sendJson(res, 200, { ok: true, artwork: ready });
  }

  if (method === "POST" && url.pathname === "/api/live/feedback") {
    return liveFeedback(res, await readJsonBody(req));
  }

  if (method === "POST" && url.pathname === "/api/artworks") {
    const body = await readJsonBody(req);
    return createArtwork(res, body);
  }

  const generateMatch = url.pathname.match(/^\/api\/artworks\/([^/]+)\/generate$/);
  if (method === "POST" && generateMatch) {
    return generateArtwork(res, generateMatch[1], await readJsonBody(req));
  }

  const downloadMatch = url.pathname.match(/^\/api\/artworks\/([^/]+)\/download$/);
  if (method === "GET" && downloadMatch) {
    return downloadArtwork(res, downloadMatch[1]);
  }

  const openKritaMatch = url.pathname.match(/^\/api\/artworks\/([^/]+)\/open-krita$/);
  if (method === "POST" && openKritaMatch) {
    return openKrita(res, openKritaMatch[1]);
  }

  const artworkMatch = url.pathname.match(/^\/api\/artworks\/([^/]+)$/);
  if (method === "GET" && artworkMatch) {
    return sendJson(res, 200, { ok: true, artwork: await loadArtworkState(artworkMatch[1]) });
  }
  if (method === "DELETE" && artworkMatch) {
    return deleteArtwork(res, artworkMatch[1]);
  }

  sendJson(res, 404, { ok: false, error: "Not found" });
}

async function createArtwork(res, body) {
  const fileName = sanitizeFileName(String(body.fileName || "artwork.png"));
  const ext = path.extname(fileName).toLowerCase();
  if (![".png", ".jpg", ".jpeg", ".webp", ".clip"].includes(ext)) {
    return sendJson(res, 400, { ok: false, error: "Supported uploads: PNG, JPG, WEBP, CLIP." });
  }

  const dataUrl = String(body.dataUrl || "");
  const match = dataUrl.match(/^data:[^;]+;base64,(.+)$/);
  if (!match) {
    return sendJson(res, 400, { ok: false, error: "Upload did not include a base64 data URL." });
  }

  const bytes = Buffer.from(match[1], "base64");
  if (bytes.length > MAX_UPLOAD_BYTES) {
    return sendJson(res, 413, { ok: false, error: "Upload is too large for this MVP." });
  }

  const id = `${timestampId()}-${crypto.randomBytes(3).toString("hex")}`;
  const dir = artworkDir(id);
  await fsp.mkdir(dir, { recursive: true });
  await fsp.mkdir(path.join(dir, "source"), { recursive: true });
  const originalPath = path.join(dir, "source", fileName);
  await fsp.writeFile(originalPath, bytes);

  const meta = {
    id,
    fileName,
    status: "preparing",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    originalPath,
    storagePath: dir,
    warnings: []
  };
  await saveMeta(id, meta);

  try {
    const result = await runWorker(["prepare", dir, originalPath, fileName], { timeoutMs: 120000 });
    meta.status = "uploaded";
    meta.prepare = parseJsonMaybe(result.stdout);
    meta.referenceUrl = `/files/${id}/reference.png`;
  } catch (error) {
    meta.status = "upload_error";
    meta.warnings.push(error.message);
  }
  meta.updatedAt = new Date().toISOString();
  await saveMeta(id, meta);
  await pruneOldArtworks();

  sendJson(res, 200, { ok: true, artwork: await loadArtworkState(id) });
}

async function liveFeedback(res, body) {
  const dataUrl = String(body.snapshotDataUrl || "");
  const match = dataUrl.match(/^data:image\/png;base64,(.+)$/);
  if (!match) {
    return sendJson(res, 400, { ok: false, error: "snapshotDataUrl must be a PNG data URL." });
  }

  const projectId = body.projectId ? String(body.projectId) : "";
  if (projectId) assertArtworkId(projectId);
  const sessionId = String(body.sessionId || "krita-live").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80);
  await fsp.mkdir(TMP_ROOT, { recursive: true });
  const snapshotPath = path.join(TMP_ROOT, `${sessionId}-${Date.now()}.png`);
  await fsp.writeFile(snapshotPath, Buffer.from(match[1], "base64"));

  try {
    const result = await runWorker(["live-feedback", ARTWORKS_ROOT, snapshotPath, projectId], { timeoutMs: 120000 });
    const feedback = parseJsonMaybe(result.stdout);
    return sendJson(res, 200, { ok: true, feedback });
  } finally {
    fsp.rm(snapshotPath, { force: true }).catch(() => {});
  }
}

async function generateArtwork(res, id, body) {
  assertArtworkId(id);
  const meta = await loadMeta(id);
  if (!meta) return sendJson(res, 404, { ok: false, error: "Artwork not found." });

  meta.status = "analyzing";
  meta.updatedAt = new Date().toISOString();
  await saveMeta(id, meta);

  const dir = artworkDir(id);
  let analysis;
  try {
    const analysisResult = await runWorker(["analyze", dir], { timeoutMs: 180000 });
    analysis = parseJsonMaybe(analysisResult.stdout);
    meta.analysis = analysis;
  } catch (error) {
    meta.status = "error";
    meta.warnings.push(`Local analysis failed: ${error.message}`);
    await saveMeta(id, meta);
    return sendJson(res, 500, { ok: false, error: error.message, artwork: await loadArtworkState(id) });
  }

  meta.status = "writing_guide";
  meta.updatedAt = new Date().toISOString();
  await saveMeta(id, meta);

  const apiMode = String(body.apiMode || "hybrid");
  const guideMode = String(body.guideMode || "overlay-heavy");
  let guide;
  if (!process.env.OPENAI_API_KEY || apiMode === "local-only") {
    guide = fallbackGuide(analysis, guideMode);
    if (!process.env.OPENAI_API_KEY) {
      guide.warnings.push("OPENAI_API_KEY is missing, so this guide used the local heuristic writer.");
    }
    if (apiMode === "local-only") {
      guide.warnings.push("Local-only mode selected, so OpenAI vision enrichment was skipped.");
    }
  } else {
    try {
      guide = await callOpenAIForGuide(dir, analysis, guideMode);
    } catch (error) {
      guide = fallbackGuide(analysis, guideMode);
      guide.warnings.push(`OpenAI call failed, local fallback used: ${error.message}`);
    }
  }

  guide = normalizeGuide(guide, analysis, guideMode);
  const guidePath = path.join(dir, "guide.json");
  await fsp.writeFile(guidePath, JSON.stringify(guide, null, 2), "utf8");

  meta.status = "rendering";
  meta.updatedAt = new Date().toISOString();
  await saveMeta(id, meta);

  try {
    await runWorker(["render", dir, guidePath], { timeoutMs: 600000 });
    meta.status = "ready";
    meta.guide = {
      title: guide.title,
      stepCount: guide.steps.length,
      complexity: guide.complexity,
      warnings: guide.warnings || []
    };
    meta.urls = artifactUrls(id, guide.steps.length);
  } catch (error) {
    meta.status = "error";
    meta.warnings.push(`Artifact rendering failed: ${error.message}`);
  }

  meta.updatedAt = new Date().toISOString();
  await saveMeta(id, meta);
  await pruneOldArtworks();
  sendJson(res, meta.status === "ready" ? 200 : 500, { ok: meta.status === "ready", artwork: await loadArtworkState(id) });
}

async function callOpenAIForGuide(dir, analysis, guideMode) {
  const referencePath = path.join(dir, "reference.png");
  const imageBase64 = await fsp.readFile(referencePath, "base64");
  const desiredSteps = analysis.suggestedStepCount || 34;
  const schema = guideSchema();
  const prompt = [
    "Create a thorough beginner Krita recreation guide for the uploaded artwork.",
    "The goal is not to trace blindly; it is to help a beginner understand how to rebuild the final output using layers, brush sizes, colors, regions, and checkpoints.",
    "Use the local image analysis JSON for exact canvas size, palette, and regions. Use the image for subject/style/vibe details.",
    `Guide mode: ${guideMode}. Desired step count: about ${desiredSteps}.`,
    "Every step must include a concrete Krita layer, brush, brush size in px, opacity, color, canvas region, instruction, checkpoint, and common mistake.",
    "Keep instructions easy to follow. Avoid vague phrases like 'add detail' without saying where/how.",
    "Do not claim the output will be identical; frame it as a close beginner recreation guide.",
    `Local analysis JSON:\n${JSON.stringify(analysis, null, 2)}`
  ].join("\n\n");

  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.OPENAI_API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: OPENAI_MODEL,
      input: [{
        role: "user",
        content: [
          { type: "input_text", text: prompt },
          { type: "input_image", image_url: `data:image/png;base64,${imageBase64}` }
        ]
      }],
      text: {
        format: {
          type: "json_schema",
          name: "krita_guide",
          strict: true,
          schema
        }
      }
    })
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error?.message || `OpenAI HTTP ${response.status}`);
  }

  const outputText = payload.output_text || collectResponseText(payload);
  if (!outputText) throw new Error("OpenAI response did not contain guide text.");
  return JSON.parse(outputText);
}

function guideSchema() {
  const region = {
    type: "object",
    additionalProperties: false,
    required: ["x", "y", "w", "h"],
    properties: {
      x: { type: "integer" },
      y: { type: "integer" },
      w: { type: "integer" },
      h: { type: "integer" }
    }
  };
  const step = {
    type: "object",
    additionalProperties: false,
    required: ["step", "title", "layer", "brush", "brushSizePx", "opacity", "color", "region", "instruction", "checkpoint", "commonMistake"],
    properties: {
      step: { type: "integer" },
      title: { type: "string" },
      layer: { type: "string" },
      brush: { type: "string" },
      brushSizePx: { type: "integer" },
      opacity: { type: "integer" },
      color: { type: "string" },
      region,
      instruction: { type: "string" },
      checkpoint: { type: "string" },
      commonMistake: { type: "string" }
    }
  };
  return {
    type: "object",
    additionalProperties: false,
    required: ["title", "summary", "canvas", "complexity", "styleNotes", "warnings", "steps"],
    properties: {
      title: { type: "string" },
      summary: { type: "string" },
      canvas: region,
      complexity: { type: "string", enum: ["simple", "medium", "complex"] },
      styleNotes: { type: "array", items: { type: "string" } },
      warnings: { type: "array", items: { type: "string" } },
      steps: { type: "array", minItems: 12, maxItems: 60, items: step }
    }
  };
}

function fallbackGuide(analysis, guideMode) {
  const canvas = { x: 0, y: 0, w: analysis.width || 1200, h: analysis.height || 1600 };
  const palette = analysis.palette || [];
  const p = (i, fallback) => palette[i]?.hex || fallback;
  const r = analysis.regions || {};
  const full = r.full || canvas;
  const upper = r.upper || full;
  const middle = r.middle || full;
  const lower = r.lower || full;
  const left = r.left || full;
  const right = r.right || full;
  const center = r.center || full;
  const components = analysis.components || [];
  const comp = (i, fallback) => components[i]?.region || fallback;
  const line = p(0, "#050507");
  const base = p(1, "#F8F6E9");
  const shadow = p(2, "#CFC7B9");
  const accent = p(3, "#58B078");
  const warm = p(4, "#F0A34C");
  const dark = p(5, "#552000");
  const steps = [
    ["Canvas setup", "Paper White", "Fill Tool", 60, 100, "#FFFFFF", canvas, "Create the canvas at the same size as the reference. Add a locked white background layer before drawing anything else.", "The whole page is white and the reference size matches.", "Starting on a transparent background makes the final look different."],
    ["Place the reference", "Reference", "Transform Tool", 1, 45, "#FFFFFF", full, "Put the original artwork on a top reference layer at low opacity, then lock it. Use it only for checking placement.", "You can see the final image faintly while sketching.", "Do not paint on the reference layer."],
    ["Big silhouette block-in", "Rough Sketch", "Basic-5 Size", 12, 35, "#777777", full, "Sketch the largest outside shape first. Use simple circles, wedges, boxes, and tubes; ignore texture and tiny details.", "The pose reads when zoomed out.", "Drawing details before the big shape is correct causes drift."],
    ["Upper structure", "Rough Sketch", "Basic-5 Size", 10, 35, "#777777", upper, "Break the upper part into simple forms. Mark head/face/upper body or main top objects with light construction lines.", "The top area is placed before clean lines.", "Pressing too hard makes it harder to correct."],
    ["Middle structure", "Rough Sketch", "Basic-5 Size", 10, 35, "#777777", middle, "Sketch the middle masses and connect them to the top. Keep the centerline and overlap relationships clear.", "The center of the artwork feels balanced.", "Do not make every edge equally important yet."],
    ["Lower structure", "Rough Sketch", "Basic-5 Size", 10, 35, "#777777", lower, "Sketch the lower forms and contact points. Use long simple lines before adding small endings.", "The artwork stands or sits in the right location.", "Tiny bottom details should wait until the silhouette is stable."],
    ["Clean outer line", "Clean Lineart", "Ink-3 Gpen", 6, 100, line, full, "Trace the outside contour with confident strokes. Use fewer, cleaner lines than the rough sketch, but keep a natural handmade edge.", "The character/object reads clearly without color.", "Over-smoothing removes the original sketchy energy."],
    ["Clean left-side details", "Clean Lineart", "Ink-2 Fineliner", 3, 100, line, left, "Add important interior lines on the left side only: folds, overlaps, feather/fur/object separations, and contour breaks.", "The left side has structure but is not crowded.", "Every small texture mark does not need a line."],
    ["Clean right-side details", "Clean Lineart", "Ink-2 Fineliner", 3, 100, line, right, "Add matching interior structure on the right side. Keep line weight lighter than the outside contour.", "Both sides feel equally finished.", "Using thick lines everywhere flattens the drawing."],
    ["Primary flat color", "Flat Colors", "Basic-1", 70, 100, base, comp(0, center), "Paint the largest main color area under the lineart. Use a hard brush or fill tool, then patch gaps by hand.", "No white holes appear inside the main shape unless intended.", "If fill leaks, close lineart gaps or paint manually."],
    ["Secondary flat color", "Flat Colors", "Basic-1", 60, 100, accent, comp(1, middle), "Paint the second biggest color group. Keep it on the same flat layer or a clipped layer for easy editing.", "The major color groups are separated cleanly.", "Do not shade before flats are readable."],
    ["Warm accent color", "Flat Colors", "Basic-1", 45, 100, warm, comp(2, upper), "Paint smaller warm/accent areas. Use the palette swatches to keep color choices consistent.", "Accent areas pop without overpowering the main form.", "Using random new colors makes the piece less cohesive."],
    ["Deep dark flats", "Flat Colors", "Basic-1", 45, 100, dark, comp(3, right), "Add the darkest flat shapes where the reference has strong dark masses.", "Dark areas anchor the artwork.", "Do not use pure black for all dark color shapes; reserve black for lineart."],
    ["Large shadow pass", "Shadows", "Basic-5 Size", 45, 35, shadow, full, "Set this layer to Multiply. Shade under overlaps, inside tucked areas, and near the bottom of forms.", "The artwork gains volume while the flats remain visible.", "Too much opacity makes the drawing muddy."],
    ["Core shadows", "Shadows", "Basic-5 Size", 28, 45, dark, middle, "Add smaller darker shadows in creases and places where forms touch. Keep strokes directional.", "Overlaps are easy to understand.", "Blending every shadow smooth removes the painted texture."],
    ["Texture strokes", "Highlights and Texture", "Basic-5 Size", 8, 60, "#FFFDF0", full, "Add short texture strokes that follow the form direction. Use small broken marks instead of long outlines.", "Texture supports the form without covering it.", "Random strokes make the surface noisy."],
    ["Highlight pass", "Highlights and Texture", "Basic-5 Size", 18, 40, "#FFFFFF", upper, "Add light touches to the top-facing and most visible areas. Keep them sparse.", "Highlights guide the eye to the focal point.", "Highlighting every edge makes the piece look plastic."],
    ["Small details", "Small Details", "Ink-2 Fineliner", 2, 100, line, comp(4, center), "Add small marks, facial/object details, lettering, claws, texture breaks, or tiny edge corrections last.", "The focal details are crisp.", "Tiny details should not fight the main silhouette."],
    ["Final check", "Small Details", "Basic-5 Size", 4, 70, line, full, "Zoom out. Compare silhouette, color balance, eye/focal detail, and shadow strength. Fix only the biggest mismatches.", "The guide recreation matches the final vibe and layout.", "Endless tiny edits can make the drawing stiff."]
  ];
  const target = clampInt(analysis.suggestedStepCount, 18, 60, 30);
  let detailIndex = 0;
  while (steps.length < target) {
    const region = comp(detailIndex % Math.max(1, components.length), [upper, middle, lower, left, right, center][detailIndex % 6]);
    const color = [line, base, shadow, accent, warm, dark][detailIndex % 6];
    const pass = detailIndex + 1;
    steps.splice(Math.max(steps.length - 1, 0), 0, [
      `Focused detail pass ${pass}`,
      pass % 2 === 0 ? "Highlights and Texture" : "Small Details",
      pass % 2 === 0 ? "Basic-5 Size" : "Ink-2 Fineliner",
      pass % 2 === 0 ? 7 : 2,
      pass % 2 === 0 ? 55 : 100,
      color,
      region,
      "Use this highlighted area as a small focused study. Match the biggest edge direction first, then add only the most visible texture or detail marks.",
      "This region feels closer to the reference without becoming busier than nearby areas.",
      "Beginners often add too many marks here. Keep the marks grouped and directional."
    ]);
    detailIndex += 1;
  }

  return {
    title: "Beginner Krita Recreation Guide",
    summary: "Local fallback guide generated from shape, color, and edge analysis.",
    canvas,
    complexity: analysis.complexity || "medium",
    styleNotes: [
      "Work from big shapes to small details.",
      "Use the generated overlays as placement guides, not as a replacement for drawing practice.",
      "Keep lineart darker than color and texture."
    ],
    warnings: [],
    steps: steps.map((s, i) => ({
      step: i + 1,
      title: s[0],
      layer: s[1],
      brush: s[2],
      brushSizePx: s[3],
      opacity: s[4],
      color: s[5],
      region: clampRegion(s[6], canvas),
      instruction: s[7],
      checkpoint: s[8],
      commonMistake: s[9]
    }))
  };
}

function normalizeGuide(guide, analysis, guideMode) {
  const canvas = { x: 0, y: 0, w: analysis.width || 1200, h: analysis.height || 1600 };
  const source = guide && Array.isArray(guide.steps) ? guide : fallbackGuide(analysis, guideMode);
  const steps = source.steps.map((step, index) => ({
    step: index + 1,
    title: String(step.title || `Step ${index + 1}`),
    layer: String(step.layer || "Guide Layer"),
    brush: String(step.brush || "Basic-5 Size"),
    brushSizePx: clampInt(step.brushSizePx, 1, 120, 8),
    opacity: clampInt(step.opacity, 5, 100, 100),
    color: normalizeHex(step.color || "#050507"),
    region: clampRegion(step.region || canvas, canvas),
    instruction: String(step.instruction || "Follow the overlay and build this part slowly."),
    checkpoint: String(step.checkpoint || "This step should match the highlighted region."),
    commonMistake: String(step.commonMistake || "Do not rush into details before placement is correct.")
  }));
  return {
    title: String(source.title || "Beginner Krita Recreation Guide"),
    summary: String(source.summary || "Step-by-step Krita guide generated from this artwork."),
    canvas,
    complexity: ["simple", "medium", "complex"].includes(source.complexity) ? source.complexity : (analysis.complexity || "medium"),
    styleNotes: Array.isArray(source.styleNotes) ? source.styleNotes.map(String).slice(0, 8) : [],
    warnings: Array.isArray(source.warnings) ? source.warnings.map(String).slice(0, 8) : [],
    steps
  };
}

async function downloadArtwork(res, id) {
  assertArtworkId(id);
  const dir = artworkDir(id);
  if (!fs.existsSync(dir)) return sendJson(res, 404, { ok: false, error: "Artwork not found." });
  const zipPath = path.join(dir, `${id}-krita-guide-pack.zip`);
  await runWorker(["zip", dir, zipPath], { timeoutMs: 180000 });
  res.writeHead(200, {
    "Content-Type": "application/zip",
    "Content-Disposition": `attachment; filename="${id}-krita-guide-pack.zip"`
  });
  fs.createReadStream(zipPath).pipe(res);
}

async function openKrita(res, id) {
  assertArtworkId(id);
  const dir = artworkDir(id);
  const reference = path.join(dir, "reference.png");
  if (!fs.existsSync(reference)) return sendJson(res, 404, { ok: false, error: "reference.png not found." });
  if (!fs.existsSync(KRITA_PATH)) {
    return sendJson(res, 400, { ok: false, error: `Krita not found at ${KRITA_PATH}. Set KRITA_PATH in .env.` });
  }
  const child = spawn(KRITA_PATH, [reference], { detached: true, stdio: "ignore" });
  child.unref();
  sendJson(res, 200, {
    ok: true,
    message: "Krita launched with the reference image. Use the generated krita/guide_loader.py script in Scripter to load overlays.",
    kritaPath: KRITA_PATH
  });
}

async function deleteArtwork(res, id) {
  assertArtworkId(id);
  const dir = artworkDir(id);
  if (!fs.existsSync(dir)) return sendJson(res, 404, { ok: false, error: "Artwork not found." });
  await fsp.rm(dir, { recursive: true, force: true });
  sendJson(res, 200, { ok: true });
}

function artifactUrls(id, stepCount) {
  const pad = (n) => String(n).padStart(3, "0");
  return {
    reference: `/files/${id}/reference.png`,
    guideJson: `/files/${id}/guide.json`,
    readme: `/files/${id}/README.md`,
    palette: `/files/${id}/palette.gpl`,
    kritaScript: `/files/${id}/krita/guide_loader.py`,
    kritaReadme: `/files/${id}/krita/README_KRITA.txt`,
    overlays: Array.from({ length: stepCount }, (_, i) => `/files/${id}/overlays/step_${pad(i + 1)}.png`),
    cards: Array.from({ length: stepCount }, (_, i) => `/files/${id}/steps/step_${pad(i + 1)}_card.png`)
  };
}

async function listArtworks() {
  await fsp.mkdir(ARTWORKS_ROOT, { recursive: true });
  const entries = await fsp.readdir(ARTWORKS_ROOT, { withFileTypes: true });
  const artworks = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const meta = await loadMeta(entry.name);
    if (meta) artworks.push(await loadArtworkState(entry.name));
  }
  artworks.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  return artworks;
}

async function loadArtworkState(id) {
  assertArtworkId(id);
  const meta = await loadMeta(id);
  if (!meta) return null;
  const guidePath = path.join(artworkDir(id), "guide.json");
  let guide = null;
  if (fs.existsSync(guidePath)) {
    guide = JSON.parse(await fsp.readFile(guidePath, "utf8"));
  }
  const state = { ...meta, guideData: guide };
  if (guide && !state.urls) state.urls = artifactUrls(id, guide.steps.length);
  return state;
}

async function loadMeta(id) {
  assertArtworkId(id);
  const file = path.join(artworkDir(id), "meta.json");
  if (!fs.existsSync(file)) return null;
  return JSON.parse(await fsp.readFile(file, "utf8"));
}

async function saveMeta(id, meta) {
  assertArtworkId(id);
  await fsp.writeFile(path.join(artworkDir(id), "meta.json"), JSON.stringify(meta, null, 2), "utf8");
}

async function pruneOldArtworks() {
  if (!KEEP_LATEST || KEEP_LATEST < 1) return;
  const artworks = await listArtworks();
  const old = artworks.slice(KEEP_LATEST);
  for (const item of old) {
    await fsp.rm(artworkDir(item.id), { recursive: true, force: true });
  }
}

function runWorker(args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [WORKER, ...args], {
      cwd: __dirname,
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`Worker timed out: ${args.join(" ")}`));
    }, options.timeoutMs || 120000);
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timeout);
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(stderr.trim() || stdout.trim() || `Worker exited with ${code}`));
    });
  });
}

function collectResponseText(payload) {
  const chunks = [];
  for (const output of payload.output || []) {
    for (const content of output.content || []) {
      if (content.type === "output_text" && content.text) chunks.push(content.text);
      if (content.type === "text" && content.text) chunks.push(content.text);
    }
  }
  return chunks.join("\n").trim();
}

function clampRegion(region, canvas) {
  const x = clampInt(region.x, canvas.x, canvas.w - 1, canvas.x);
  const y = clampInt(region.y, canvas.y, canvas.h - 1, canvas.y);
  const w = clampInt(region.w, 1, canvas.w - x, Math.max(1, canvas.w - x));
  const h = clampInt(region.h, 1, canvas.h - y, Math.max(1, canvas.h - y));
  return { x, y, w, h };
}

function clampInt(value, min, max, fallback) {
  const n = Number.isFinite(Number(value)) ? Math.round(Number(value)) : fallback;
  return Math.max(min, Math.min(max, n));
}

function normalizeHex(value) {
  const text = String(value || "").trim();
  const short = text.match(/^#([0-9a-fA-F]{3})$/);
  if (short) return `#${short[1].split("").map((c) => c + c).join("").toUpperCase()}`;
  const full = text.match(/^#([0-9a-fA-F]{6})$/);
  return full ? `#${full[1].toUpperCase()}` : "#050507";
}

function parseJsonMaybe(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return null;
  return JSON.parse(trimmed);
}

async function readJsonBody(req) {
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > MAX_UPLOAD_BYTES * 1.5) throw new Error("Request body is too large.");
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

async function serveStatic(res, filePath) {
  return serveFile(res, filePath);
}

async function serveSafeFile(res, root, relative) {
  const safeRoot = path.resolve(root);
  const filePath = path.resolve(safeRoot, relative || "");
  if (!filePath.startsWith(safeRoot)) return sendJson(res, 403, { ok: false, error: "Forbidden path." });
  return serveFile(res, filePath);
}

async function serveFile(res, filePath) {
  if (!fs.existsSync(filePath) || (await fsp.stat(filePath)).isDirectory()) {
    return sendJson(res, 404, { ok: false, error: "File not found." });
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(filePath).toLowerCase()] || "application/octet-stream" });
  fs.createReadStream(filePath).pipe(res);
}

function sendJson(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

async function ensureDirs() {
  await fsp.mkdir(ARTWORKS_ROOT, { recursive: true });
  await fsp.mkdir(TMP_ROOT, { recursive: true });
}

function artworkDir(id) {
  assertArtworkId(id);
  return path.join(ARTWORKS_ROOT, id);
}

function assertArtworkId(id) {
  if (!/^[a-zA-Z0-9_-]+$/.test(String(id || ""))) throw new Error("Invalid artwork id.");
}

function sanitizeFileName(name) {
  const cleaned = path.basename(name).replace(/[^a-zA-Z0-9._ -]/g, "_").trim();
  return cleaned || "artwork.png";
}

function timestampId() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    const key = trimmed.slice(0, index).trim();
    let value = trimmed.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}
