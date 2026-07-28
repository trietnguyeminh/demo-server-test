const state = { profile: "auto" };
const $ = (id) => document.getElementById(id);
const apiBase = () => $("apiBase").value.trim().replace(/\/$/, "");
const apiUrl = (path) => `${apiBase()}${path}`;

function setStatus(message, isError = false) {
  const area = $("statusArea");
  area.textContent = message;
  area.classList.toggle("error", isError);
}

function ms(value) {
  return value == null ? "—" : `${Number(value).toFixed(1)} ms`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

function renderRoute(route) {
  $("routePanel").hidden = false;
  $("complexityBadge").textContent = `complexity ${(route.complexity * 100).toFixed(0)}%`;
  const items = [
    ["Visual", route.visual],
    ["OCR", route.ocr],
    ["ASR", route.asr],
    ["API planner", route.api_planner],
  ];
  $("routeCards").innerHTML = items.map(([name, item]) => `
    <article class="route-card ${item.enabled ? "enabled" : ""}">
      <h3>${item.enabled ? "✓" : "–"} ${name}</h3>
      <strong>${(item.confidence * 100).toFixed(0)}% confidence</strong>
      <div class="confidence"><span style="width:${item.confidence * 100}%"></span></div>
      <p>${item.reason}</p>
    </article>
  `).join("");
}

function renderResults(hits, base) {
  $("resultCount").textContent = `${hits.length} kết quả`;
  if (!hits.length) {
    $("results").innerHTML = '<div class="empty">Không có candidate phù hợp.</div>';
    return;
  }
  $("results").innerHTML = hits.map((hit) => {
    const image = `${base}${hit.image_url}`;
    const evidence = hit.ocr_text || hit.asr_text || "Visual evidence";
    return `
      <article class="result-card">
        <img src="${image}" alt="Frame ${hit.item_id}" loading="lazy">
        <div class="result-body">
          <div class="result-title">
            <strong>#${hit.rank} ${hit.video_id}</strong>
            <span>${hit.pts_time.toFixed(2)}s</span>
          </div>
          <div class="tags">${hit.modalities.map((m) => `<span class="tag">${m}</span>`).join("")}</div>
          <p class="evidence">${evidence}</p>
        </div>
      </article>
    `;
  }).join("");
}

async function checkHealth() {
  try {
    setStatus("Đang kiểm tra server…");
    const data = await requestJson("/api/health");
    $("healthDot").className = "dot good";
    $("healthText").textContent = "Server sẵn sàng";
    $("backendText").textContent = `${data.backend.mode} • visual ${data.backend.visual_ready ? "ready" : "off"}`;
    setStatus("Kết nối server thành công.");
  } catch (error) {
    $("healthDot").className = "dot bad";
    $("healthText").textContent = "Không kết nối được";
    $("backendText").textContent = error.message;
    setStatus(error.message, true);
  }
}

async function search() {
  const button = $("searchButton");
  button.disabled = true;
  button.textContent = "Đang tìm…";
  setStatus("Router đang chọn modality và chạy retrieval song song…");
  try {
    const payload = {
      query: $("query").value.trim(),
      profile: state.profile,
      top_k: Number($("topK").value),
      ocr: $("ocrMode").value,
      asr: $("asrMode").value,
      api_planner: $("apiPlannerMode").value,
      adaptive_fallback: $("adaptiveFallback").checked,
    };
    const data = await requestJson("/api/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderRoute(data.route);
    renderResults(data.hits, apiBase());
    $("summaryGrid").hidden = false;
    $("totalLatency").textContent = ms(data.latency_ms.total_ms);
    $("visualLatency").textContent = ms(data.latency_ms.visual_ms);
    $("ocrLatency").textContent = ms(data.latency_ms.ocr_ms ?? data.latency_ms.ocr_fallback_ms);
    $("asrLatency").textContent = ms(data.latency_ms.asr_ms);
    const suffix = data.warnings.length ? ` • ${data.warnings.join(" ")}` : "";
    setStatus(`Hoàn tất bằng backend ${data.backend_mode}.${suffix}`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Tìm kiếm";
  }
}

document.querySelectorAll("[data-profile]").forEach((button) => {
  button.addEventListener("click", () => {
    state.profile = button.dataset.profile;
    document.querySelectorAll("[data-profile]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

$("healthButton").addEventListener("click", checkHealth);
$("searchButton").addEventListener("click", search);
$("query").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") search();
});

const queryApi = new URLSearchParams(location.search).get("api");
if (queryApi) $("apiBase").value = queryApi;
checkHealth();
