const initialParams = new URLSearchParams(window.location.search);

const state = {
  profile: "auto",
  sessionId: null,
  agentReady: false,
  lastHits: [],
  selectedHit: null,
  selectedCopyText: "",
  pendingItemId: initialParams.get("item"),
};

const $ = (id) => document.getElementById(id);
const apiBase = () => $("apiBase").value.trim().replace(/\/$/, "");
const apiUrl = (path) => `${apiBase()}${path}`;
const latencyFormatter = new Intl.NumberFormat("vi-VN", {
  maximumFractionDigits: 1,
});
const countFormatter = new Intl.NumberFormat("vi-VN");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const PROFILE_HELP = {
  fast: "Visual-first; ưu tiên latency.",
  auto: "Cân bằng Visual, OCR và ASR.",
  accurate: "Candidate pool lớn hơn; ưu tiên recall.",
};

const AGENT_MODE_HELP = {
  local: "Không gọi model ngoài. Router local + retrieval.",
  planner: "Một API call để resolve query, sau đó retrieval local.",
  full: "Planner + retrieval + answer dựa trên evidence.",
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
  area.dataset.state = isError ? "error" : (message ? "active" : "idle");
}

function formatLatency(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return number >= 1000
    ? `${latencyFormatter.format(number / 1000)} s`
    : `${latencyFormatter.format(number)} ms`;
}

function formatTimestamp(value) {
  const seconds = Math.max(0, Number(value) || 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const secText = secs.toFixed(2).padStart(5, "0");
  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${secText}`;
  }
  return `${String(minutes).padStart(2, "0")}:${secText}`;
}

function absoluteImageUrl(hit) {
  const relative = hit?.image_url || `/api/frame/${encodeURIComponent(hit?.item_id || "")}`;
  const value = `${apiBase()}${relative}`;
  try {
    return new URL(value, window.location.href).href;
  } catch (_) {
    return value;
  }
}

function keyframeValue(hit) {
  const explicit =
    hit?.keyframe_id
    ?? hit?.keyframe
    ?? hit?.frame_id
    ?? hit?.frame_index
    ?? hit?.frame_idx;
  if (explicit !== undefined && explicit !== null && String(explicit) !== "") {
    return String(explicit);
  }
  const itemId = String(hit?.item_id || "");
  const doubleUnderscore = itemId.match(/__(\d+)$/);
  if (doubleUnderscore) return doubleUnderscore[1];
  const trailingDigits = itemId.match(/(?:_|-)(\d+)$/);
  return trailingDigits ? trailingDigits[1] : "—";
}

function selectionText(hit) {
  const imageLocation = absoluteImageUrl(hit);
  return [
    `Rank: #${hit.rank ?? "—"}`,
    `Video ID: ${hit.video_id ?? "—"}`,
    `Item ID: ${hit.item_id ?? "—"}`,
    `Keyframe: ${keyframeValue(hit)}`,
    `Timestamp: ${formatTimestamp(hit.pts_time)} (${Number(hit.pts_time || 0).toFixed(2)}s)`,
    `Vị trí ảnh: ${imageLocation}`,
  ].join("\n");
}

function submissionText(hit) {
  return `${hit?.video_id ?? ""},${keyframeValue(hit)}`;
}

function syncUrlState() {
  const params = new URLSearchParams(window.location.search);
  const values = {
    api: apiBase(),
    profile: state.profile,
    ocr: $("ocrMode").value,
    asr: $("asrMode").value,
    k: $("topK").value,
    item: state.selectedHit?.item_id || "",
  };

  Object.entries(values).forEach(([key, value]) => {
    if (value && !(key === "profile" && value === "auto")) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
  });

  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  try {
    window.history.replaceState(null, "", nextUrl);
  } catch (_) {
    // Sandboxed previews may have an opaque origin; runtime HTTP pages do not.
  }
}

async function copyText(text, feedbackElement = null) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (_) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    copied = document.execCommand("copy");
    textarea.remove();
  }
  if (feedbackElement) {
    feedbackElement.textContent = copied ? "Đã sao chép" : "Không sao chép được";
    window.setTimeout(() => { feedbackElement.textContent = ""; }, 1800);
  }
  return copied;
}

async function requestJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

function addMessage(role, text, meta = "") {
  const article = document.createElement("article");
  article.className = `message message-${role}`;
  article.innerHTML = `
    <div class="message-role">${role === "user" ? "Bạn" : "Agent"}</div>
    <p>${escapeHtml(text)}</p>
    ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
  `;
  $("chatMessages").appendChild(article);
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
}

function addFrameSelectionMessage(hit) {
  const text = selectionText(hit);
  let article = document.getElementById("selectedFrameMessage");
  if (!article) {
    article = document.createElement("article");
    article.id = "selectedFrameMessage";
    article.className = "message message-selection";
    $("chatMessages").appendChild(article);
  }

  article.replaceChildren();

  const role = document.createElement("div");
  role.className = "message-role";
  role.textContent = "Selected shot";

  const intro = document.createElement("p");
  intro.textContent = "Frame đang chọn — sao chép trực tiếp từ shot slip này:";

  const pre = document.createElement("pre");
  pre.className = "selection-block";
  pre.textContent = text;

  const actions = document.createElement("div");
  actions.className = "selection-actions";

  const copyAllButton = document.createElement("button");
  copyAllButton.type = "button";
  copyAllButton.className = "button button-primary button-small";
  copyAllButton.textContent = "Sao chép toàn bộ";

  const copyPairButton = document.createElement("button");
  copyPairButton.type = "button";
  copyPairButton.className = "button button-secondary button-small";
  copyPairButton.textContent = "Copy Video,Keyframe";

  const askButton = document.createElement("button");
  askButton.type = "button";
  askButton.className = "button button-quiet button-small";
  askButton.textContent = "Hỏi Agent về frame";

  const feedback = document.createElement("span");
  feedback.className = "copy-feedback";
  feedback.setAttribute("aria-live", "polite");

  copyAllButton.addEventListener("click", () => copyText(text, feedback));
  copyPairButton.addEventListener("click", () => copyText(submissionText(hit), feedback));
  askButton.addEventListener("click", () => {
    $("agentMessage").value =
      `Phân tích frame ${hit.item_id}, video ${hit.video_id}, `
      + `keyframe ${keyframeValue(hit)} tại ${Number(hit.pts_time || 0).toFixed(2)}s.`;
    $("agentMessage").focus();
  });

  actions.append(copyAllButton, copyPairButton, askButton, feedback);
  article.append(role, intro, pre, actions);
  $("chatMessages").appendChild(article);
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
}

function renderRoute(route) {
  if (!route) {
    $("routePanel").hidden = true;
    return;
  }
  $("routePanel").hidden = false;
  $("complexityBadge").textContent =
    `độ phức tạp ${(Number(route.complexity || 0) * 100).toFixed(0)}%`;

  const items = [
    ["Visual", route.visual],
    ["OCR", route.ocr],
    ["ASR", route.asr],
    ["API planner", route.api_planner],
  ];

  $("routeCards").innerHTML = items.map(([name, item = {}]) => {
    const score = Number(item.routing_score ?? item.confidence ?? 0);
    const stateLabel =
      EXECUTION_LABELS[item.execution_state]
      || item.execution_state
      || "—";
    return `
      <article class="route-card ${item.enabled ? "enabled" : ""}">
        <div class="route-card-head">
          <h3>${item.enabled ? "✓" : "–"} ${escapeHtml(name)}</h3>
          <span class="state-badge">${escapeHtml(stateLabel)}</span>
        </div>
        <strong>${(score * 100).toFixed(0)}% routing score</strong>
        <div class="confidence">
          <span style="width:${Math.max(0, Math.min(100, score * 100))}%"></span>
        </div>
        <p>${escapeHtml(item.reason || "")}</p>
      </article>
    `;
  }).join("");
}

function renderAgentPlan(plan, agent) {
  if (!plan || !agent) return;
  $("agentPlanPanel").hidden = false;
  $("resolvedQuery").textContent = plan.resolved_query || "—";
  $("agentIntent").textContent =
    `${plan.intent || "—"} • search ${plan.search_required ? "yes" : "no"}`;
  $("agentModalities").textContent =
    `OCR ${String(plan.ocr || "—").toUpperCase()} • ASR ${String(plan.asr || "—").toUpperCase()}`;
  $("agentRationale").textContent = plan.rationale || "—";
  $("agentModelBadge").textContent = [
    agent.provider,
    agent.model || "local",
    agent.fallback_used ? "fallback" : "active",
  ].filter(Boolean).join(" • ");
}

function updateSelectedPreview(hit) {
  const imageUrl = absoluteImageUrl(hit);
  const image = $("selectedPreviewImage");

  $("stageEmpty").hidden = true;
  $("selectedPreview").hidden = false;
  image.classList.remove("is-loaded");
  image.src = imageUrl;
  image.alt = `Keyframe ${keyframeValue(hit)} của video ${hit.video_id || ""}`;
  image.onload = () => image.classList.add("is-loaded");

  const rankText = hit.rank == null ? "—" : String(hit.rank).padStart(2, "0");
  $("selectedPreviewRank").textContent = `RANK ${rankText}`;
  $("selectedPreviewTimecode").textContent = formatTimestamp(hit.pts_time);
  $("selectedPreviewTitle").textContent =
    `${hit.video_id || "—"} / KF ${keyframeValue(hit)}`;
  $("selectedPreviewMeta").textContent =
    `${hit.item_id || "—"} • ${formatTimestamp(hit.pts_time)} • ${Number(hit.pts_time || 0).toFixed(2)}s`;
  $("selectedFrameBadge").textContent =
    `Đã chọn ${hit.item_id || "frame"}`;
  $("openSelectedLink").href = imageUrl;
  $("selectedCopyFeedback").textContent = "";
  state.selectedCopyText = selectionText(hit);
}

function selectHit(hit, card = null) {
  state.selectedHit = hit;
  const selectedIndex = state.lastHits.indexOf(hit);

  document.querySelectorAll(".frame-card").forEach((element) => {
    element.classList.toggle(
      "is-selected",
      Number(element.dataset.index) === selectedIndex,
    );
  });

  document.querySelectorAll(".frame-select").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      Number(button.dataset.index) === selectedIndex ? "true" : "false",
    );
  });

  if (card) card.classList.add("is-selected");
  updateSelectedPreview(hit);
  addFrameSelectionMessage(hit);
  syncUrlState();
}

function renderResults(hits, base, endToEndMs) {
  state.lastHits = Array.isArray(hits) ? hits : [];
  state.selectedHit = null;
  state.selectedCopyText = "";
  $("selectedPreview").hidden = true;
  $("stageEmpty").hidden = false;
  $("selectedFrameBadge").textContent = "Chưa chọn frame";
  $("resultCount").textContent =
    `${countFormatter.format(state.lastHits.length)} kết quả • ${formatLatency(endToEndMs)}`;

  if (!state.lastHits.length) {
    $("results").innerHTML = `
      <div class="empty-state">
        <span class="empty-index">00</span>
        <div><strong>Không có candidate phù hợp</strong><p>Thử đổi query hoặc bật OCR / ASR.</p></div>
      </div>`;
    syncUrlState();
    return;
  }

  $("results").innerHTML = state.lastHits.map((hit, index) => {
    const image = absoluteImageUrl(hit);
    const evidence = hit.ocr_text || hit.asr_text || "Visual evidence";
    const timestamp = formatTimestamp(hit.pts_time);
    const modalities = (hit.modalities || [])
      .map((mode) => `<span class="modality-tag">${escapeHtml(mode)}</span>`)
      .join("");

    return `
      <article class="frame-card" data-index="${index}">
        <button class="frame-select" type="button" data-index="${index}"
          aria-pressed="false"
          aria-label="Chọn frame ${escapeHtml(hit.item_id)} của video ${escapeHtml(hit.video_id)} tại ${timestamp}">
          <div class="frame-image">
            <img src="${escapeHtml(image)}" width="640" height="360"
              alt="Keyframe ${escapeHtml(keyframeValue(hit))} của video ${escapeHtml(hit.video_id)}"
              loading="lazy" decoding="async">
            <span class="frame-index">#${escapeHtml(hit.rank)}</span>
            <time class="frame-time">${timestamp}</time>
          </div>
          <div class="frame-body">
            <div class="frame-title-row">
              <strong>${escapeHtml(hit.video_id)}</strong>
              <span>KF ${escapeHtml(keyframeValue(hit))}</span>
            </div>
            <div class="frame-id" title="${escapeHtml(hit.item_id)}">${escapeHtml(hit.item_id)}</div>
            <div class="modality-tags">${modalities}</div>
            <p class="frame-evidence">${escapeHtml(evidence)}</p>
          </div>
        </button>
        <footer class="frame-footer">
          <span>Chọn để gửi metadata vào chat</span>
          <a class="frame-open-link" href="${escapeHtml(image)}"
            target="_blank" rel="noopener">Mở ảnh</a>
        </footer>
      </article>`;
  }).join("");

  document.querySelectorAll(".frame-select").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.index);
      const hit = state.lastHits[index];
      const card = button.closest(".frame-card");
      if (hit) selectHit(hit, card);
    });
  });

  const requestedItem = state.pendingItemId;
  state.pendingItemId = null;
  if (requestedItem) {
    const index = state.lastHits.findIndex((hit) => hit.item_id === requestedItem);
    if (index >= 0) {
      const button = document.querySelector(`.frame-select[data-index="${index}"]`);
      selectHit(state.lastHits[index], button?.closest(".frame-card") || null);
    }
  }
}

function updateSearchMetrics(searchData, endToEndMs) {
  $("summaryGrid").hidden = false;
  const serverMs = Number(searchData?.latency_ms?.total_ms || 0);
  $("endToEndLatency").textContent = formatLatency(endToEndMs);
  $("serverLatency").textContent = formatLatency(serverMs);
  $("visualLatency").textContent = formatLatency(searchData?.latency_ms?.visual_ms);
  $("ocrLatency").textContent = formatLatency(
    searchData?.latency_ms?.ocr_ms ?? searchData?.latency_ms?.ocr_fallback_ms
  );
  $("asrLatency").textContent = formatLatency(searchData?.latency_ms?.asr_ms);
}

function updateAgentMetrics(agentLatency = {}, endToEndMs = null) {
  $("summaryGrid").hidden = false;
  if (endToEndMs != null) $("endToEndLatency").textContent = formatLatency(endToEndMs);
  $("agentTotalLatency").textContent = formatLatency(agentLatency.total_ms);
  $("plannerLatency").textContent = formatLatency(agentLatency.planner_ms);
  $("searchLatency").textContent = formatLatency(agentLatency.search_ms);
  $("answerLatency").textContent = formatLatency(agentLatency.answer_ms);
}

function updateProfileHelp() {
  $("profileHelp").textContent = PROFILE_HELP[state.profile];
}

function updateAgentModeHelp() {
  $("agentModeHelp").textContent = AGENT_MODE_HELP[$("agentMode").value];
}

function populateProviders(agent) {
  const select = $("agentProviderSelect");
  const previous = select.value || "auto";
  const providers = Array.isArray(agent?.providers) ? agent.providers : [];
  select.innerHTML = '<option value="auto">Auto — provider sẵn sàng đầu tiên</option>'
    + providers.map((provider) => {
      const disabled = provider.ready ? "" : " disabled";
      const suffix = provider.ready ? "ready" : "chưa cấu hình";
      return `<option value="${escapeHtml(provider.id)}"${disabled}>`
        + `${escapeHtml(provider.label)} — ${suffix}</option>`;
    }).join("");
  if ([...select.options].some((option) => option.value === previous && !option.disabled)) {
    select.value = previous;
  } else {
    select.value = "auto";
  }
}

function updateProviderHint(agent) {
  const ready = agent?.ready_providers || [];
  $("agentModels").textContent = ready.length ? ready.join(", ") : "Không có";
  $("agentKeyStatus").textContent = `${agent?.ready_provider_count || 0} provider ready`;
}

async function checkHealth() {
  try {
    setStatus("Đang kiểm tra server và agent…");
    const data = await requestJson("/api/health");
    state.agentReady = Boolean(data.agent?.ready);
    $("healthDot").className = "dot good";
    $("healthText").textContent = "Server sẵn sàng";
    $("backendText").textContent = [
      data.backend.mode,
      `visual ${data.backend.visual_ready ? "ready" : "off"}`,
      `OCR ${data.backend.ocr_ready ? "ready" : "off"}`,
      `ASR ${data.backend.asr_ready ? "ready" : "off"}`,
    ].join(" • ");

    const agent = data.agent || {};
    $("agentText").textContent = agent.ready
      ? `${agent.ready_provider_count || 0} external provider ready`
      : `Agent local fallback: ${agent.error || "external disabled"}`;
    $("agentProvider").textContent =
      `${agent.default_provider || "auto"} • ${agent.ready ? "ready" : "local fallback"}`;
    populateProviders(agent);
    updateProviderHint(agent);
    setStatus(agent.ready
      ? "Kết nối server và external agent thành công."
      : "Server hoạt động; Agent dùng local fallback.");
  } catch (error) {
    $("healthDot").className = "dot bad";
    $("healthText").textContent = "Không kết nối được";
    $("backendText").textContent = error.message;
    $("agentText").textContent = "Agent: unknown";
    setStatus(error.message, true);
  }
}

function sharedRetrievalPayload() {
  return {
    profile: state.profile,
    top_k: Number($("topK").value),
    ocr: $("ocrMode").value,
    asr: $("asrMode").value,
    adaptive_fallback: $("adaptiveFallback").checked,
  };
}

async function directSearch() {
  const button = $("searchButton");
  const query = $("agentMessage").value.trim();
  if (!query) {
    setStatus("Hãy nhập truy vấn trong ô chat.", true);
    return;
  }

  $("query").value = query;
  addMessage("user", query, "Direct search");
  button.disabled = true;
  button.textContent = "Đang tìm…";
  $("evidenceColumn").setAttribute("aria-busy", "true");
  setStatus("Đang chạy Visual / OCR / ASR…");
  const started = performance.now();

  try {
    const data = await requestJson("/api/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        ...sharedRetrievalPayload(),
        api_planner: "off",
      }),
    });

    const received = performance.now();
    renderRoute(data.route);
    renderResults(data.hits, apiBase(), received - started);
    const finished = performance.now();
    updateSearchMetrics(data, finished - started);
    $("agentTotalLatency").textContent = "—";
    $("plannerLatency").textContent = "—";
    $("searchLatency").textContent = "—";
    $("answerLatency").textContent = "—";

    addMessage(
      "assistant",
      `Tìm trực tiếp hoàn tất: ${data.hits.length} candidate. Chọn ảnh bên phải để đưa metadata vào chat.`,
      formatLatency(finished - started),
    );
    setStatus(`Direct search hoàn tất sau ${formatLatency(finished - started)}.`);
  } catch (error) {
    addMessage("assistant", `Lỗi direct search: ${error.message}`);
    setStatus(`${error.message} Kiểm tra Server API và trạng thái tunnel.`, true);
  } finally {
    $("evidenceColumn").setAttribute("aria-busy", "false");
    button.disabled = false;
    button.textContent = "Tìm trực tiếp";
  }
}

async function agentChat() {
  const button = $("agentSendButton");
  const message = $("agentMessage").value.trim();
  if (!message) {
    setStatus("Hãy nhập tin nhắn cho Agent.", true);
    return;
  }

  $("query").value = message;
  addMessage("user", message);
  $("agentMessage").value = "";
  button.disabled = true;
  button.textContent = "Đang xử lý…";
  $("evidenceColumn").setAttribute("aria-busy", "true");
  setStatus("Agent đang lập kế hoạch và gọi retrieval tools…");
  const started = performance.now();

  try {
    const data = await requestJson("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        message,
        mode: $("agentMode").value,
        provider: $("agentProviderSelect").value,
        model_tier: $("agentModelTier").value,
        model: $("agentModelOverride").value.trim() || null,
        ...sharedRetrievalPayload(),
      }),
    });

    state.sessionId = data.session_id;
    const received = performance.now();
    const meta = [
      data.agent.provider,
      data.agent.model || "local",
      formatLatency(data.latency_ms.total_ms),
      data.agent.fallback_used ? "fallback" : "",
    ].filter(Boolean).join(" • ");

    addMessage("assistant", data.reply, meta);
    renderAgentPlan(data.plan, data.agent);

    const searchData = data.search;
    if (searchData) {
      renderRoute(searchData.route);
      renderResults(searchData.hits, apiBase(), received - started);
    }

    const finished = performance.now();
    updateAgentMetrics(data.latency_ms, finished - started);
    if (searchData) updateSearchMetrics(searchData, finished - started);

    const warningText = data.warnings?.length
      ? ` • ${data.warnings.join(" ")}`
      : "";
    setStatus(`Agent hoàn tất sau ${formatLatency(finished - started)}${warningText}`);
  } catch (error) {
    addMessage("assistant", `Lỗi: ${error.message}`);
    setStatus(`${error.message} Kiểm tra provider, Server API và tunnel.`, true);
  } finally {
    $("evidenceColumn").setAttribute("aria-busy", "false");
    button.disabled = false;
    button.textContent = "Gửi Agent";
  }
}

async function resetSession() {
  if (state.sessionId) {
    try {
      await requestJson(`/api/agent/session/${encodeURIComponent(state.sessionId)}`, {
        method: "DELETE",
      });
    } catch (_) {
      // Local reset remains safe.
    }
  }
  state.sessionId = null;
  state.selectedHit = null;
  state.selectedCopyText = "";
  $("chatMessages").innerHTML = `
    <article class="message message-assistant">
      <div class="message-role">Agent</div>
      <p>Đã tạo cuộc trò chuyện mới. Chọn ảnh sẽ tiếp tục đưa metadata vào đây.</p>
    </article>
  `;
  $("agentPlanPanel").hidden = true;
  document.getElementById("selectedFrameMessage")?.remove();
  setStatus("Đã xóa session Agent.");
}

function applyUrlState() {
  const params = new URLSearchParams(window.location.search);
  const api = params.get("api");
  const profile = params.get("profile");
  const ocr = params.get("ocr");
  const asr = params.get("asr");
  const topK = Number(params.get("k"));

  if (api) $("apiBase").value = api;
  if (["fast", "auto", "accurate"].includes(profile)) {
    $("profileSelect").value = profile;
  }
  if (["off", "auto", "on"].includes(ocr)) $("ocrMode").value = ocr;
  if (["off", "auto", "on"].includes(asr)) $("asrMode").value = asr;
  if (Number.isFinite(topK) && topK >= 1 && topK <= 100) {
    $("topK").value = String(topK);
  }
}

$("profileSelect").addEventListener("change", () => {
  state.profile = $("profileSelect").value;
  updateProfileHelp();
  syncUrlState();
});
$("ocrMode").addEventListener("change", syncUrlState);
$("asrMode").addEventListener("change", syncUrlState);
$("topK").addEventListener("change", syncUrlState);
$("apiBase").addEventListener("change", () => {
  syncUrlState();
  checkHealth();
});
$("healthButton").addEventListener("click", checkHealth);
$("searchButton").addEventListener("click", directSearch);
$("chatComposer").addEventListener("submit", (event) => {
  event.preventDefault();
  agentChat();
});
$("newSessionButton").addEventListener("click", resetSession);
$("agentMode").addEventListener("change", updateAgentModeHelp);
$("copySelectedButton").addEventListener("click", () => {
  if (!state.selectedCopyText) return;
  copyText(state.selectedCopyText, $("selectedCopyFeedback"));
});
$("copySubmissionButton").addEventListener("click", () => {
  if (!state.selectedHit) return;
  copyText(submissionText(state.selectedHit), $("selectedCopyFeedback"));
});

$("agentMessage").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    agentChat();
  }
});

applyUrlState();
state.profile = $("profileSelect").value;
updateProfileHelp();
updateAgentModeHelp();
checkHealth();
