const state = {
  profile: "auto",
  sessionId: null,
  agentReady: false,
  activeTab: "agent",
};

const $ = (id) => document.getElementById(id);
const apiBase = () => $("apiBase").value.trim().replace(/\/$/, "");
const apiUrl = (path) => `${apiBase()}${path}`;

const PROFILE_HELP = {
  fast: "Visual-first; OCR chỉ khi tín hiệu chữ mạnh. Ưu tiên candidate sớm.",
  auto: "Visual + OCR nhẹ song song; cân bằng recall và latency.",
  accurate: "Pool lớn hơn; ưu tiên recall, chậm hơn.",
};

const AGENT_MODE_HELP = {
  local: "Không gọi model ngoài. Router local + retrieval.",
  planner: "Một API call để hiểu hội thoại/resolve query, sau đó retrieval local.",
  full: "Hai API call: planner trước retrieval và trả lời dựa trên evidence sau retrieval.",
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
  return number >= 1000
    ? `${(number / 1000).toFixed(2)} s`
    : `${number.toFixed(1)} ms`;
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
  article.className = `message ${role}`;
  article.innerHTML = `
    <div class="message-role">${role === "user" ? "Bạn" : "Agent"}</div>
    <p>${escapeHtml(text)}</p>
    ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
  `;
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
    `độ phức tạp ${(route.complexity * 100).toFixed(0)}%`;

  const items = [
    ["Visual", route.visual],
    ["OCR", route.ocr],
    ["ASR", route.asr],
    ["API planner", route.api_planner],
  ];

  $("routeCards").innerHTML = items.map(([name, item]) => {
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
        <p>${escapeHtml(item.reason)}</p>
      </article>
    `;
  }).join("");
}

function renderAgentPlan(plan, agent) {
  $("agentPlanPanel").hidden = false;
  $("resolvedQuery").textContent = plan.resolved_query;
  $("agentIntent").textContent =
    `${plan.intent} • search ${plan.search_required ? "yes" : "no"}`;
  $("agentModalities").textContent =
    `OCR ${plan.ocr.toUpperCase()} • ASR ${plan.asr.toUpperCase()}`;
  $("agentRationale").textContent = plan.rationale || "—";
  $("agentModelBadge").textContent = [
    agent.provider,
    agent.model || "local",
    agent.fallback_used ? "fallback" : "active",
  ].join(" • ");
}

function renderResults(hits, base, endToEndMs) {
  $("resultCount").textContent =
    `${hits.length} kết quả • ${formatLatency(endToEndMs)}`;

  if (!hits.length) {
    $("results").innerHTML =
      '<div class="empty">Không có candidate phù hợp.</div>';
    return;
  }

  $("results").innerHTML = hits.map((hit) => {
    const image = `${base}${hit.image_url}`;
    const evidence =
      hit.ocr_text || hit.asr_text || "Visual evidence";
    const timestamp = formatTimestamp(hit.pts_time);

    return `
      <article class="result-card">
        <img src="${escapeHtml(image)}"
          alt="Frame ${escapeHtml(hit.item_id)}" loading="lazy">
        <div class="result-body">
          <div class="result-title">
            <strong>#${hit.rank} ${escapeHtml(hit.video_id)}</strong>
            <span title="Timestamp trong video">
              Video ${timestamp}
            </span>
          </div>
          <div class="tags">
            ${hit.modalities.map(
              (mode) => `<span class="tag">${escapeHtml(mode)}</span>`
            ).join("")}
          </div>
          <p class="evidence">${escapeHtml(evidence)}</p>
          <button type="button" class="ask-hit secondary small"
            data-rank="${hit.rank}" data-video="${escapeHtml(hit.video_id)}"
            data-time="${hit.pts_time}">
            Hỏi agent về kết quả này
          </button>
        </div>
      </article>
    `;
  }).join("");

  document.querySelectorAll(".ask-hit").forEach((button) => {
    button.addEventListener("click", () => {
      switchTab("agent");
      $("agentMessage").value =
        `Phân tích kỹ kết quả rank ${button.dataset.rank}, `
        + `video ${button.dataset.video} tại ${button.dataset.time}s.`;
      $("agentMessage").focus();
    });
  });
}

function updateSearchMetrics(searchData, endToEndMs) {
  $("summaryGrid").hidden = false;
  const serverMs = Number(searchData?.latency_ms?.total_ms || 0);

  $("endToEndLatency").textContent = formatLatency(endToEndMs);
  $("serverLatency").textContent = formatLatency(serverMs);
  $("visualLatency").textContent =
    formatLatency(searchData?.latency_ms?.visual_ms);
  $("ocrLatency").textContent =
    formatLatency(
      searchData?.latency_ms?.ocr_ms
      ?? searchData?.latency_ms?.ocr_fallback_ms
    );
  $("asrLatency").textContent =
    formatLatency(searchData?.latency_ms?.asr_ms);
}

function updateAgentMetrics(agentLatency = {}, endToEndMs = null) {
  $("summaryGrid").hidden = false;
  if (endToEndMs != null) {
    $("endToEndLatency").textContent = formatLatency(endToEndMs);
  }
  $("agentTotalLatency").textContent =
    formatLatency(agentLatency.total_ms);
  $("plannerLatency").textContent =
    formatLatency(agentLatency.planner_ms);
  $("searchLatency").textContent =
    formatLatency(agentLatency.search_ms);
  $("answerLatency").textContent =
    formatLatency(agentLatency.answer_ms);
}

function updateProfileHelp() {
  $("profileHelp").textContent = PROFILE_HELP[state.profile];
}

function updateAgentModeHelp() {
  $("agentModeHelp").textContent =
    AGENT_MODE_HELP[$("agentMode").value];
}

function switchTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  $("agentWorkspace").hidden = tab !== "agent";
  $("searchWorkspace").hidden = tab !== "search";
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
      ? `Agent ${agent.provider} ready`
      : `Agent chưa sẵn sàng: ${agent.error || "disabled"}`;
    $("agentProvider").textContent =
      `${agent.provider || "—"} • ${agent.ready ? "ready" : "not ready"}`;
    $("agentModels").textContent =
      `${agent.fast_model || "—"} / ${agent.quality_model || "—"}`;
    $("agentKeyStatus").textContent =
      agent.api_key_configured ? "Configured in backend" : "Missing";

    setStatus(
      agent.ready
        ? "Kết nối server và external agent thành công."
        : "Server hoạt động; agent sẽ dùng local fallback."
    );
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
  const query = $("query").value.trim();
  if (!query) {
    setStatus("Hãy nhập truy vấn.", true);
    return;
  }

  button.disabled = true;
  button.textContent = "Đang tìm…";
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

    setStatus(
      `Direct search hoàn tất: ${data.hits.length} kết quả `
      + `sau ${formatLatency(finished - started)}.`
    );
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Tìm kiếm";
  }
}

async function agentChat() {
  const button = $("agentSendButton");
  const message = $("agentMessage").value.trim();
  if (!message) {
    setStatus("Hãy nhập tin nhắn cho agent.", true);
    return;
  }

  addMessage("user", message);
  $("agentMessage").value = "";
  button.disabled = true;
  button.textContent = "Agent đang xử lý…";
  setStatus("Agent đang lập kế hoạch và gọi retrieval tools…");

  const started = performance.now();

  try {
    const data = await requestJson("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        message,
        mode: $("agentMode").value,
        model_tier: $("agentModelTier").value,
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
    if (searchData) {
      updateSearchMetrics(searchData, finished - started);
    }

    const warningText = data.warnings.length
      ? ` • ${data.warnings.join(" ")}`
      : "";
    setStatus(
      `Agent hoàn tất sau ${formatLatency(finished - started)}`
      + warningText
    );
  } catch (error) {
    addMessage("assistant", `Lỗi: ${error.message}`);
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Gửi cho agent";
  }
}

async function resetSession() {
  if (state.sessionId) {
    try {
      await requestJson(
        `/api/agent/session/${encodeURIComponent(state.sessionId)}`,
        { method: "DELETE" }
      );
    } catch (_) {
      // A local UI reset is still safe.
    }
  }
  state.sessionId = null;
  $("chatMessages").innerHTML = `
    <article class="message assistant">
      <div class="message-role">Agent</div>
      <p>Đã tạo cuộc trò chuyện mới.</p>
    </article>
  `;
  $("agentPlanPanel").hidden = true;
  setStatus("Đã xóa session agent.");
}

document.querySelectorAll("[data-profile]").forEach((button) => {
  button.addEventListener("click", () => {
    state.profile = button.dataset.profile;
    document.querySelectorAll("[data-profile]").forEach((item) => {
      item.classList.remove("active");
    });
    button.classList.add("active");
    updateProfileHelp();
  });
});

document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});

$("healthButton").addEventListener("click", checkHealth);
$("searchButton").addEventListener("click", directSearch);
$("agentSendButton").addEventListener("click", agentChat);
$("newSessionButton").addEventListener("click", resetSession);
$("agentMode").addEventListener("change", updateAgentModeHelp);

$("query").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    directSearch();
  }
});

$("agentMessage").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    agentChat();
  }
});

const queryApi = new URLSearchParams(location.search).get("api");
if (queryApi) $("apiBase").value = queryApi;

updateProfileHelp();
updateAgentModeHelp();
switchTab("agent");
checkHealth();
