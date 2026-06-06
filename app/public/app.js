const state = {
  health: null,
  file: null,
  artwork: null,
  liveSessions: [],
  activeTab: "steps",
  busy: false
};

const els = {
  healthLine: document.querySelector("#healthLine"),
  fileInput: document.querySelector("#fileInput"),
  dropzone: document.querySelector("#dropzone"),
  guideMode: document.querySelector("#guideMode"),
  localOnly: document.querySelector("#localOnly"),
  uploadBtn: document.querySelector("#uploadBtn"),
  generateBtn: document.querySelector("#generateBtn"),
  openKritaBtn: document.querySelector("#openKritaBtn"),
  refreshBtn: document.querySelector("#refreshBtn"),
  statusBox: document.querySelector("#statusBox"),
  historyList: document.querySelector("#historyList"),
  previewFrame: document.querySelector(".preview-frame"),
  previewImage: document.querySelector("#previewImage"),
  metaGrid: document.querySelector("#metaGrid"),
  tabs: [...document.querySelectorAll(".tab")],
  panels: [...document.querySelectorAll(".tab-panel")],
  stepsOutput: document.querySelector("#stepsOutput"),
  overlaysOutput: document.querySelector("#overlaysOutput"),
  kritaOutput: document.querySelector("#kritaOutput"),
  liveOutput: document.querySelector("#liveOutput"),
  storageOutput: document.querySelector("#storageOutput"),
  downloadOutput: document.querySelector("#downloadOutput")
};

init();

function init() {
  bindEvents();
  refreshHealth();
  refreshHistory();
}

function bindEvents() {
  els.fileInput.addEventListener("change", () => setFile(els.fileInput.files[0]));
  els.uploadBtn.addEventListener("click", uploadSelectedFile);
  els.generateBtn.addEventListener("click", generateGuide);
  els.openKritaBtn.addEventListener("click", openKrita);
  els.refreshBtn.addEventListener("click", refreshHistory);

  els.dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    els.dropzone.classList.add("drag");
  });
  els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("drag"));
  els.dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    els.dropzone.classList.remove("drag");
    setFile(event.dataTransfer.files[0]);
  });

  for (const tab of els.tabs) {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  }
}

async function refreshHealth() {
  try {
    const data = await api("/api/health");
    state.health = data;
    els.healthLine.textContent = `${data.storageRoot} | OpenAI ${data.openaiConfigured ? "configured" : "not configured"} | ${data.model}`;
    renderStorage();
  } catch (error) {
    els.healthLine.textContent = `Server error: ${error.message}`;
  }
}

async function refreshHistory() {
  try {
    const data = await api("/api/artworks");
    els.historyList.innerHTML = "";
    if (!data.artworks.length) {
      els.historyList.innerHTML = `<div class="empty-output">No projects yet.</div>`;
      return;
    }
    for (const artwork of data.artworks) {
      const button = document.createElement("button");
      button.className = "history-item";
      button.innerHTML = `
        <strong>${escapeHtml(artwork.fileName || artwork.id)}</strong>
        <span>${escapeHtml(artwork.status || "unknown")} | ${escapeHtml(artwork.id)}</span>
      `;
      button.addEventListener("click", () => loadArtwork(artwork.id));
      els.historyList.appendChild(button);
    }
  } catch (error) {
    els.historyList.innerHTML = `<div class="empty-output">History failed: ${escapeHtml(error.message)}</div>`;
  }
}

function setFile(file) {
  state.file = file || null;
  els.uploadBtn.disabled = !state.file || state.busy;
  if (!state.file) {
    setStatus("No artwork selected.");
    return;
  }
  setStatus(`Selected: ${state.file.name}\nSize: ${formatBytes(state.file.size)}`);
}

async function uploadSelectedFile() {
  if (!state.file) return;
  setBusy(true, "Uploading artwork...");
  try {
    const dataUrl = await readFileAsDataUrl(state.file);
    const data = await api("/api/artworks", {
      method: "POST",
      body: JSON.stringify({ fileName: state.file.name, dataUrl })
    });
    state.artwork = data.artwork;
    state.liveSessions = [];
    setStatus(`Uploaded: ${state.artwork.fileName}\nStatus: ${state.artwork.status}`);
    renderArtwork();
    await refreshHistory();
  } catch (error) {
    setStatus(`Upload failed:\n${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function generateGuide() {
  if (!state.artwork) return;
  setBusy(true, "Generating guide. This can take a few minutes for complex artwork...");
  const started = Date.now();
  const timer = setInterval(() => {
    const seconds = Math.round((Date.now() - started) / 1000);
    setStatus(`Generating guide...\nElapsed: ${seconds}s\nOpenAI: ${els.localOnly.checked ? "local-only fallback" : "hybrid if key exists"}`);
  }, 1000);
  try {
    const data = await api(`/api/artworks/${state.artwork.id}/generate`, {
      method: "POST",
      body: JSON.stringify({
        guideMode: els.guideMode.value,
        apiMode: els.localOnly.checked ? "local-only" : "hybrid"
      })
    });
    state.artwork = data.artwork;
    setStatus(`Guide ready.\nSteps: ${state.artwork.guide?.stepCount || state.artwork.guideData?.steps?.length || 0}`);
    renderArtwork();
    await refreshHistory();
  } catch (error) {
    setStatus(`Generation failed:\n${error.message}`);
    if (state.artwork?.id) await loadArtwork(state.artwork.id);
  } finally {
    clearInterval(timer);
    setBusy(false);
  }
}

async function loadArtwork(id) {
  setBusy(true, "Loading project...");
  try {
    const data = await api(`/api/artworks/${id}`);
    state.artwork = data.artwork;
    state.liveSessions = [];
    setStatus(`Loaded: ${state.artwork.fileName}\nStatus: ${state.artwork.status}`);
    renderArtwork();
  } catch (error) {
    setStatus(`Load failed:\n${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function openKrita() {
  if (!state.artwork) return;
  setBusy(true, "Opening Krita...");
  try {
    const data = await api(`/api/artworks/${state.artwork.id}/open-krita`, { method: "POST" });
    setStatus(data.message || "Krita launch requested.");
  } catch (error) {
    setStatus(`Open Krita failed:\n${error.message}`);
  } finally {
    setBusy(false);
  }
}

function renderArtwork() {
  const artwork = state.artwork;
  if (!artwork) return;
  if (artwork.referenceUrl || artwork.urls?.reference) {
    els.previewImage.src = `${artwork.referenceUrl || artwork.urls.reference}?t=${Date.now()}`;
    els.previewFrame.classList.add("has-image");
  }
  const stepCount = artwork.guide?.stepCount || artwork.guideData?.steps?.length || 0;
  els.generateBtn.disabled = state.busy || !artwork || artwork.status === "upload_error";
  els.openKritaBtn.disabled = state.busy || !artwork || !artwork.urls?.kritaScript;
  renderMeta(artwork, stepCount);
  renderSteps();
  renderOverlays();
  renderKrita();
  renderLiveSessions();
  renderStorage();
  renderDownload();
}

function renderMeta(artwork, stepCount) {
  const cells = [
    ["Project", artwork.id],
    ["Status", artwork.status],
    ["Steps", String(stepCount)],
    ["Storage", artwork.storagePath || state.health?.storageRoot || "D:\\data"]
  ];
  els.metaGrid.innerHTML = cells.map(([label, value]) => `
    <div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value || "-")}</span></div>
  `).join("");
}

function renderSteps() {
  const artwork = state.artwork;
  if (!artwork?.guideData?.steps?.length || !artwork.urls?.cards?.length) {
    els.stepsOutput.className = "empty-output";
    els.stepsOutput.textContent = "Step cards will appear here after generation.";
    return;
  }
  els.stepsOutput.className = "step-list";
  els.stepsOutput.innerHTML = artwork.guideData.steps.map((step, index) => `
    <article class="step-card">
      <img src="${artwork.urls.cards[index]}" alt="Step ${step.step} card" loading="lazy">
      <footer>
        <strong>${String(step.step).padStart(2, "0")}. ${escapeHtml(step.title)}</strong>
        <a href="${artwork.urls.cards[index]}" target="_blank" rel="noreferrer">Open</a>
      </footer>
    </article>
  `).join("");
}

function renderOverlays() {
  const artwork = state.artwork;
  if (!artwork?.urls?.overlays?.length) {
    els.overlaysOutput.className = "empty-output";
    els.overlaysOutput.textContent = "Overlay PNGs will appear here after generation.";
    return;
  }
  els.overlaysOutput.className = "overlay-grid";
  els.overlaysOutput.innerHTML = artwork.urls.overlays.map((url, index) => `
    <article class="overlay-tile">
      <img src="${url}" alt="Overlay ${index + 1}" loading="lazy">
      <footer>
        <strong>Overlay ${String(index + 1).padStart(3, "0")}</strong>
        <a href="${url}" target="_blank" rel="noreferrer">Open</a>
      </footer>
    </article>
  `).join("");
}

function renderKrita() {
  const artwork = state.artwork;
  if (!artwork?.urls?.kritaScript) {
    els.kritaOutput.className = "empty-output";
    els.kritaOutput.textContent = "Krita helper details will appear here after generation.";
    return;
  }
  els.kritaOutput.className = "info-panel";
  const scriptPath = `${artwork.storagePath}\\krita\\guide_loader.py`;
  els.kritaOutput.innerHTML = `
    ${renderWarnings(artwork)}
    <div>
      <strong>Krita script</strong>
      <code>${escapeHtml(scriptPath)}</code>
    </div>
    <div class="button-row">
      <button id="kritaTabOpenBtn">Open Krita</button>
      <a class="link-button" href="${artwork.urls.kritaScript}" target="_blank" rel="noreferrer">View script</a>
      <a class="link-button" href="${artwork.urls.kritaReadme}" target="_blank" rel="noreferrer">Krita notes</a>
      <a class="link-button" href="${artwork.urls.palette}" target="_blank" rel="noreferrer">Palette</a>
    </div>
    <p>Run the script from Krita Scripter. If file layers fail, import the overlays manually from the project folder.</p>
  `;
  document.querySelector("#kritaTabOpenBtn")?.addEventListener("click", openKrita);
}

async function renderLiveSessions() {
  const artwork = state.artwork;
  if (!artwork?.id) {
    els.liveOutput.className = "empty-output";
    els.liveOutput.textContent = "Load an artwork to view live telemetry sessions.";
    return;
  }
  els.liveOutput.className = "info-panel";
  els.liveOutput.innerHTML = `
    <div class="button-row">
      <button id="refreshLiveBtn">Refresh live sessions</button>
      <a class="link-button" href="/api/artworks/${artwork.id}/live-sessions" target="_blank" rel="noreferrer">Open API list</a>
    </div>
    <div class="empty-output">Loading live telemetry sessions...</div>
  `;
  document.querySelector("#refreshLiveBtn")?.addEventListener("click", () => loadLiveSessions(true));
  await loadLiveSessions(false);
}

async function loadLiveSessions(forceStatus) {
  const artwork = state.artwork;
  if (!artwork?.id) return;
  if (forceStatus) setStatus("Refreshing live telemetry sessions...");
  try {
    const data = await api(`/api/artworks/${artwork.id}/live-sessions`);
    state.liveSessions = data.sessions || [];
    renderLiveSessionList();
  } catch (error) {
    els.liveOutput.className = "empty-output";
    els.liveOutput.textContent = `Live telemetry failed: ${error.message}`;
  }
}

function renderLiveSessionList() {
  const artwork = state.artwork;
  const sessions = state.liveSessions || [];
  els.liveOutput.className = "info-panel";
  if (!sessions.length) {
    els.liveOutput.innerHTML = `
      <div class="button-row">
        <button id="refreshLiveBtn">Refresh live sessions</button>
      </div>
      <div class="empty-output">No live sessions recorded yet. In Krita, start the Live Coach with Record input metrics checked, then draw.</div>
    `;
    document.querySelector("#refreshLiveBtn")?.addEventListener("click", () => loadLiveSessions(true));
    return;
  }
  els.liveOutput.innerHTML = `
    <div class="button-row">
      <button id="refreshLiveBtn">Refresh live sessions</button>
      <a class="link-button" href="/api/artworks/${artwork.id}/live-sessions" target="_blank" rel="noreferrer">Open API list</a>
    </div>
    <div class="telemetry-grid">
      ${sessions.map((session, index) => `
        <button class="telemetry-session ${index === 0 ? "active" : ""}" data-session="${escapeHtml(session.sessionId)}">
          <strong>${escapeHtml(session.sessionId)}</strong>
          <span>${escapeHtml(session.updatedAt)} | ${formatBytes(session.bytes)}</span>
        </button>
      `).join("")}
    </div>
    <div id="liveSessionDetail" class="telemetry-detail">Select a session to view recorded input metrics.</div>
  `;
  document.querySelector("#refreshLiveBtn")?.addEventListener("click", () => loadLiveSessions(true));
  for (const button of document.querySelectorAll(".telemetry-session")) {
    button.addEventListener("click", () => loadLiveSessionDetail(button.dataset.session));
  }
  loadLiveSessionDetail(sessions[0].sessionId);
}

async function loadLiveSessionDetail(sessionId) {
  const artwork = state.artwork;
  if (!artwork?.id || !sessionId) return;
  const detail = document.querySelector("#liveSessionDetail");
  if (detail) detail.textContent = "Loading session...";
  try {
    const data = await api(`/api/artworks/${artwork.id}/live-sessions/${encodeURIComponent(sessionId)}`);
    renderLiveSessionDetail(data.sessionId, data.records || []);
  } catch (error) {
    if (detail) detail.textContent = `Session failed: ${error.message}`;
  }
}

function renderLiveSessionDetail(sessionId, records) {
  const detail = document.querySelector("#liveSessionDetail");
  if (!detail) return;
  if (!records.length) {
    detail.innerHTML = `<div class="empty-output">Session ${escapeHtml(sessionId)} has no records.</div>`;
    return;
  }
  const latest = records[records.length - 1];
  const summary = latest.telemetry?.summary || {};
  const context = latest.telemetry?.context || {};
  const matrix = latest.telemetry?.capabilityMatrix || {};
  const feedback = latest.feedback || {};
  const strokes = latest.telemetry?.strokes || [];
  detail.innerHTML = `
    <div class="telemetry-summary">
      ${metricCard("Records", records.length)}
      ${metricCard("Events", summary.eventCount || 0)}
      ${metricCard("Strokes", summary.strokeCount || strokes.length || 0)}
      ${metricCard("Pressure", matrix.pressure ? "Yes" : "No")}
      ${metricCard("Active layer", context.activeLayer?.name || "-")}
      ${metricCard("Category", context.activeCategory || "-")}
    </div>
    <div class="telemetry-columns">
      <section>
        <h3>Latest Coach State</h3>
        <dl>
          ${defRow("Step", `${feedback.step || "-"} ${feedback.stepTitle || ""}`)}
          ${defRow("Progress", `${feedback.progressPercent ?? "-"}%`)}
          ${defRow("Stage", feedback.stageInfo?.stage || "-")}
          ${defRow("Assessment", context.assessmentMode || "-")}
        </dl>
      </section>
      <section>
        <h3>Stroke Metrics</h3>
        <dl>
          ${defRow("Distance", `${round(summary.distancePx)} px`)}
          ${defRow("Avg stroke distance", `${round(summary.avgStrokeDistancePx)} px`)}
          ${defRow("Max speed", `${round(summary.maxStrokeSpeedPxPerSec)} px/s`)}
          ${defRow("Pressure avg/max", `${round(summary.avgPressure)} / ${round(summary.maxPressure)}`)}
        </dl>
      </section>
      <section>
        <h3>Brush / Tool</h3>
        <dl>
          ${defRow("Brush", context.tool?.brushPreset || "-")}
          ${defRow("Brush size", context.tool?.brushSize ?? "-")}
          ${defRow("Opacity / Flow", `${context.tool?.paintingOpacity ?? "-"} / ${context.tool?.paintingFlow ?? "-"}`)}
          ${defRow("Foreground", context.tool?.foregroundColor || "-")}
        </dl>
      </section>
    </div>
    <section>
      <h3>Capability Matrix</h3>
      <div class="capability-grid">
        ${renderCapability("Visual snapshot", matrix.visualSnapshot)}
        ${renderCapability("Raw input events", matrix.rawInputEvents)}
        ${renderCapability("Stroke summaries", matrix.strokeSummaries)}
        ${renderCapability("Pressure", matrix.pressure)}
        ${renderCapability("Tilt", matrix.tilt)}
        ${renderCapability("Rotation", matrix.rotation)}
        ${renderCapability("Tangential pressure", matrix.tangentialPressure)}
        ${renderCapability("Buttons", matrix.buttons)}
        ${renderCapability("Active layer", matrix.activeLayer)}
        ${renderCapability("Layer categories", matrix.visibleLayerCategories)}
        ${renderCapability("Brush preset", matrix.brushPreset)}
        ${renderCapability("Brush size", matrix.brushSize)}
      </div>
    </section>
    <section>
      <h3>Visible Categories</h3>
      <pre>${escapeHtml(JSON.stringify(context.visibleCategoryCounts || {}, null, 2))}</pre>
    </section>
    <section>
      <h3>Unsupported / Not Guessed</h3>
      <ul>${(matrix.unsupported || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>None reported.</li>"}</ul>
    </section>
    <div class="button-row">
      <a class="link-button" href="/api/artworks/${state.artwork.id}/live-sessions/${encodeURIComponent(sessionId)}" target="_blank" rel="noreferrer">Open full JSON</a>
    </div>
  `;
}

function metricCard(label, value) {
  return `<div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value)}</span></div>`;
}

function defRow(label, value) {
  return `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`;
}

function renderCapability(label, value) {
  return `<div class="${value ? "yes" : "no"}"><strong>${escapeHtml(label)}</strong><span>${value ? "Captured" : "Unavailable"}</span></div>`;
}

function round(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 100) / 100 : 0;
}

function renderStorage() {
  const artwork = state.artwork;
  els.storageOutput.className = "info-panel";
  els.storageOutput.innerHTML = `
    <div>
      <strong>Root</strong>
      <code>${escapeHtml(state.health?.root || "D:\\data\\krita-guide-agent")}</code>
    </div>
    <div>
      <strong>Storage</strong>
      <code>${escapeHtml(state.health?.storageRoot || "D:\\data\\krita-guide-agent\\storage")}</code>
    </div>
    <div>
      <strong>Current project</strong>
      <code>${escapeHtml(artwork?.storagePath || "No project loaded")}</code>
    </div>
    <div>
      <strong>Retention</strong>
      <code>Keep latest ${escapeHtml(String(state.health?.keepLatest || 20))} projects</code>
    </div>
    ${artwork ? `<button id="deleteBtn" class="danger">Delete this project</button>` : ""}
  `;
  document.querySelector("#deleteBtn")?.addEventListener("click", deleteCurrentArtwork);
}

function renderDownload() {
  const artwork = state.artwork;
  if (!artwork?.urls?.guideJson) {
    els.downloadOutput.className = "empty-output";
    els.downloadOutput.textContent = "Download link will appear here after generation.";
    return;
  }
  els.downloadOutput.className = "info-panel";
  els.downloadOutput.innerHTML = `
    <div class="button-row">
      <a class="link-button primary" href="/api/artworks/${artwork.id}/download">Download guide pack ZIP</a>
      <a class="link-button" href="${artwork.urls.readme}" target="_blank" rel="noreferrer">README</a>
      <a class="link-button" href="${artwork.urls.guideJson}" target="_blank" rel="noreferrer">guide.json</a>
      <a class="link-button" href="${artwork.urls.reference}" target="_blank" rel="noreferrer">Reference</a>
    </div>
  `;
}

async function deleteCurrentArtwork() {
  if (!state.artwork) return;
  if (!confirm(`Delete ${state.artwork.id}?`)) return;
  setBusy(true, "Deleting project...");
  try {
    await api(`/api/artworks/${state.artwork.id}`, { method: "DELETE" });
    state.artwork = null;
    els.previewFrame.classList.remove("has-image");
    renderMeta({ id: "None", status: "Idle", storagePath: state.health?.storageRoot }, 0);
    setStatus("Project deleted.");
    await refreshHistory();
  } catch (error) {
    setStatus(`Delete failed:\n${error.message}`);
  } finally {
    setBusy(false);
  }
}

function renderWarnings(artwork) {
  const warnings = [
    ...(artwork.warnings || []),
    ...(artwork.guideData?.warnings || []),
    ...(artwork.guide?.warnings || [])
  ].filter(Boolean);
  if (!warnings.length) return "";
  return `<div class="warning">${warnings.map(escapeHtml).join("<br>")}</div>`;
}

function setTab(name) {
  state.activeTab = name;
  for (const tab of els.tabs) tab.classList.toggle("active", tab.dataset.tab === name);
  for (const panel of els.panels) panel.classList.toggle("active", panel.id === `tab-${name}`);
  if (name === "live" && state.artwork?.id) loadLiveSessions(false);
}

function setBusy(value, message) {
  state.busy = value;
  if (message) setStatus(message);
  els.uploadBtn.disabled = value || !state.file;
  els.generateBtn.disabled = value || !state.artwork || state.artwork.status === "upload_error";
  els.openKritaBtn.disabled = value || !state.artwork?.urls?.kritaScript;
}

function setStatus(text) {
  els.statusBox.textContent = text;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("File read failed."));
    reader.readAsDataURL(file);
  });
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.message || `HTTP ${response.status}`);
  }
  return data;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}
