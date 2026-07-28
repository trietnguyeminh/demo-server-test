const state = { profile: "auto", apiPlannerReady: false };
const $ = (id) => document.getElementById(id);
const apiBase = () => $("apiBase").value.trim().replace(/\/$/, "");
const apiUrl = (path) => `${apiBase()}${path}`;

const PROFILE_HELP = {
  fast: "Visual-first. OCR chỉ chạy khi tín hiệu chữ rất mạnh; ưu tiên candidate sớm.",
  auto: "Visual và OCR index nhẹ chạy song song. OCR yếu chỉ bổ sung candidate visual, không tự chen kết quả rác.",
  accurate: "Pool candidate lớn hơn; OCR có thể đưa candidate độc lập vào ranking. Chậm hơn nhưng ưu tiên recall.",
};

const EXECUTION_LABELS = {
  always_on: "Luôn bật",
  forced_on: "Ép bật",
  forced_off: "Ép tắt",
  auto_on: "Auto bật",
  auto_off: "Auto tắt",
  auto_parallel: "Auto song song",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(message, isError = false) {
  const area = $("statusArea");
  area.textContent = message;
  area.classList.toggle("error", isError);
}

function formatLatency(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return number >= 1000 ? `${(number / 1000).toFixed(2)} s` : `${number.toFixed(1)} ms`;
}

function formatTimestamp(value) {
  const seconds = Math.max(0, Number(value) || 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const secText = secs.toFixed(2).padStart(5, "0");
  if (hours > 0) return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${secText}`;
  return `${String(minutes).padStart(2, "0")}:${secText}`;
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
  $("complexityBadge").textContent = `độ phức tạp ${(route.complexity * 100).toFixed(0)}%`;
  const items = [
    ["Visual", route.visual],
    ["OCR", route.ocr],
    ["ASR", route.asr],
    ["API planner", route.api_planner],
  ];
  $("routeCards").innerHTML = items.map(([name, item]) => {
    const score = Number(item.routing_score ?? item.confidence ?? 0);
    const stateLabel = EXECUTION_LABELS[item.execution_state] || item.execution_state || "—";
    return `
      <article class="route-card ${item.enabled ? "enabled" : ""}">
        <div class="route-card-head">
          <h3>${item.enabled ? "✓" : "–"} ${escapeHtml(name)}</h3>
          <span class="state-badge">${escapeHtml(stateLabel)}</span>
        </div>
        <strong>${(score * 100).toFixed(0)}% routing score</strong>
        <div class="confidence"><span style="width:${Math.max(0, Math.min(100, score * 100))}%"></span></div>
        <p>${escapeHtml(item.reason)}</p>
      </article>
    `;
  }).join("");
}

function renderResults(hits, base, endToEndMs) {
  $("resultCount").textContent = `${hits.length} kết quả • ${formatLatency(endToEndMs)}`;
  if (!hits.length) {
    $("results").innerHTML = '<div class="empty">Không có candidate phù hợp.</div>';
    return;
  }
  $("results").innerHTML = hits.map((hit) => {
    const image = `${base}${hit.image_url}`;
    const evidence = hit.ocr_text || hit.asr_text || "Visual evidence";
    const timestamp = formatTimestamp(hit.pts_time);
    return `
      <article class="result-card">
        <img src="${escapeHtml(image)}" alt="Frame ${escapeHtml(hit.item_id)}" loading="lazy">
        <div class="result-body">
          <div class="result-title">
            <strong>#${hit.rank} ${escapeHtml(hit.video_id)}</strong>
            <span title="Timestamp trong video, không phải latency">Video ${timestamp}</span>
          </div>
          <div class="tags">${hit.modalities.map((m) => `<span class="tag">${escapeHtml(m)}</span>`).join("")}</div>
          <p class="evidence">${escapeHtml(evidence)}</p>
        </div>
      </article>
    `;
  }).join("");
}

function updateProfileHelp() {
  $("profileHelp").textContent = PROFILE_HELP[state.profile];
}

async function checkHealth() {
  try {
    setStatus("Đang kiểm tra server…");
    const data = await requestJson("/api/health");
    state.apiPlannerReady = Boolean(data.api_planner_ready);
    $("healthDot").className = "dot good";
    $("healthText").textContent = "Server sẵn sàng";
    $("backendText").textContent = [
      data.backend.mode,
      `visual ${data.backend.visual_ready ? "ready" : "off"}`,
      `OCR ${data.backend.ocr_ready ? "ready" : "off"}`,
      `ASR ${data.backend.asr_ready ? "ready" : "off"}`,
    ].join(" • ");
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
  const query = $("query").value.trim();
  if (!query) {
    setStatus("Hãy nhập truy vấn.", true);
    return;
  }

  button.disabled = true;
  button.textContent = "Đang tìm…";
  setStatus("Router đang chọn modality và chạy retrieval song song…");
  const clientStarted = performance.now();

  try {
    const payload = {
      query,
      profile: state.profile,
      top_k: Number($("topK").value),
      ocr: $("ocrMode").value,
      asr: $("asrMode").value,
      api_planner: "off",
      adaptive_fallback: $("adaptiveFallback").checked,
    };
    const data = await requestJson("/api/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const responseReceived = performance.now();

    renderRoute(data.route);
    renderResults(data.hits, apiBase(), responseReceived - clientStarted);
    const renderFinished = performance.now();

    const serverMs = Number(data.latency_ms.total_ms || 0);
    const roundTripMs = responseReceived - clientStarted;
    const endToEndMs = renderFinished - clientStarted;
    const networkMs = Math.max(0, roundTripMs - serverMs);

    $("summaryGrid").hidden = false;
    $("endToEndLatency").textContent = formatLatency(endToEndMs);
    $("serverLatency").textContent = formatLatency(serverMs);
    $("networkLatency").textContent = formatLatency(networkMs);
    $("visualLatency").textContent = formatLatency(data.latency_ms.visual_ms);
    $("ocrLatency").textContent = formatLatency(data.latency_ms.ocr_ms ?? data.latency_ms.ocr_fallback_ms);
    $("asrLatency").textContent = formatLatency(data.latency_ms.asr_ms);

    const suffix = data.warnings.length ? ` • ${data.warnings.join(" ")}` : "";
    setStatus(
      `Hoàn tất: ${data.hits.length} kết quả sau ${formatLatency(endToEndMs)}; server xử lý ${formatLatency(serverMs)}.${suffix}`
    );
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
    updateProfileHelp();
  });
});

$("healthButton").addEventListener("click", checkHealth);
$("searchButton").addEventListener("click", search);
$("query").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") search();
});

const queryApi = new URLSearchParams(location.search).get("api");
if (queryApi) $("apiBase").value = queryApi;
updateProfileHelp();
checkHealth();
