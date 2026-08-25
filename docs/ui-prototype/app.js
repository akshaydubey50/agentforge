const navGroups = [
  {
    title: "Work",
    items: [
      { id: "mission", label: "Mission Control", icon: "MC", badge: "3" },
      { id: "workspace", label: "Workspace", icon: "WS", badge: "live" },
      { id: "runs", label: "Runs", icon: "RN" },
      { id: "approvals", label: "Approvals", icon: "AP", badge: "2" },
    ],
  },
  {
    title: "Capabilities",
    items: [
      { id: "knowledge", label: "Knowledge", icon: "KG" },
      { id: "memory", label: "Memory", icon: "MM", badge: "148" },
      { id: "tools", label: "Tools", icon: "TL", badge: "18" },
      { id: "mcp", label: "MCP", icon: "MP", badge: "1" },
      { id: "integrations", label: "Integrations", icon: "IN" },
    ],
  },
  {
    title: "Quality",
    items: [
      { id: "observability", label: "Observability", icon: "OB" },
      { id: "evals", label: "Evals", icon: "EV" },
    ],
  },
  {
    title: "Govern",
    items: [
      { id: "policies", label: "Policies", icon: "PO" },
      { id: "security", label: "Security", icon: "SC" },
    ],
  },
  {
    title: "System",
    items: [{ id: "settings", label: "Settings", icon: "ST" }],
  },
];

const screenMeta = {
  mission: ["Work", "Mission Control"],
  workspace: ["Work", "Agent Workspace"],
  runs: ["Work", "Runs"],
  knowledge: ["Capabilities", "Knowledge"],
  memory: ["Capabilities", "Memory"],
  tools: ["Capabilities", "Tools"],
  mcp: ["Capabilities", "MCP"],
  integrations: ["Capabilities", "Integrations"],
  approvals: ["Work", "Approvals"],
  policies: ["Govern", "Policies"],
  evals: ["Quality", "Evals"],
  observability: ["Quality", "Observability"],
  security: ["Govern", "Security"],
  settings: ["System", "Settings"],
};

const visualFamilies = {
  goal: "system",
  plan: "reasoning",
  llm: "reasoning",
  router: "system",
  tool: "action",
  subagent: "action",
  memory: "context",
  knowledge: "context",
  policy: "control",
  approval: "control",
  execution: "action",
  verification: "quality",
  recovery: "system",
  synthesis: "reasoning",
  final: "system",
};

const familyLabels = {
  reasoning: "Reasoning",
  action: "Action",
  context: "Context",
  control: "Control",
  quality: "Quality",
  system: "System",
};

const nodes = [
  {
    id: "goal",
    type: "goal",
    actor: "user",
    label: "Goal",
    x: 12,
    y: 20,
    details: {
      Overview: "Find agentic AI developments today, verify them, and prepare LinkedIn content ideas.",
      Input: "User goal submitted from Workspace chat.",
      Metrics: "0 tokens. Runtime accepted the run.",
    },
  },
  {
    id: "plan",
    type: "plan",
    actor: "llm",
    label: "Planner",
    x: 32,
    y: 20,
    details: {
      Overview: "LLM produces a structured plan with research, memory, synthesis, policy, and verification steps.",
      Output: "Plan: search current sources, retrieve prior AgentForge preferences, synthesize ideas, draft content.",
      Metrics: "Model gpt-4o-mini, 1.8s, 1,420 input tokens, 220 output tokens.",
    },
  },
  {
    id: "web",
    type: "tool",
    actor: "tool",
    label: "Web Search",
    x: 52,
    y: 12,
    details: {
      Overview: "Native web_search tool gathers current source-backed developments.",
      Input: "Query: top agentic AI developments today.",
      Output: "4 candidate developments, 7 source snippets, 2 discarded as stale.",
      Metrics: "2.1s, read action, no approval required.",
    },
  },
  {
    id: "memory",
    type: "memory",
    actor: "memory",
    label: "Memory",
    x: 52,
    y: 30,
    details: {
      Overview: "Durable memory retrieval selects relevant preferences and previous architecture decisions.",
      Memory: "Selected 3 memories. Dropped 5 lower-ranked candidates due to budget.",
      Context: "940 tokens included, 420 tokens dropped.",
    },
  },
  {
    id: "evidence",
    type: "knowledge",
    actor: "knowledge",
    label: "Evidence",
    x: 68,
    y: 20,
    details: {
      Overview: "Runtime groups web evidence and memory context before synthesis.",
      Evidence: "3 verified source clusters, 1 conflict flagged for review.",
      Metrics: "Context block tagged as untrusted external evidence.",
    },
  },
  {
    id: "llm",
    type: "llm",
    actor: "llm",
    label: "Synthesis",
    x: 84,
    y: 20,
    details: {
      Overview: "LLM drafts verified content ideas using selected evidence and memory.",
      Context: "72% context window used. Reserved output: 4,000 tokens.",
      Output: "5 content angles, citations summary, risk notes.",
      Metrics: "2.7s, 3,210 input tokens, 680 output tokens.",
    },
  },
  {
    id: "draft",
    type: "tool",
    actor: "tool",
    label: "Gmail Draft",
    x: 32,
    y: 57,
    details: {
      Overview: "Agent proposes creating a Gmail draft with content ideas for review.",
      Input: "Recipient: self. Subject: Agentic AI content ideas.",
      Policy: "External write candidate. Runtime must evaluate policy before execution.",
    },
  },
  {
    id: "policy",
    type: "policy",
    actor: "runtime",
    label: "Policy",
    x: 52,
    y: 57,
    details: {
      Overview: "AgentForge deterministic policy evaluates the validated tool call.",
      Policy: "Decision: REQUIRE_APPROVAL. Action: external_write. Risk: medium.",
      Trace: "Reason: Gmail draft changes an external service and cannot be undone by AgentForge.",
    },
  },
  {
    id: "approval",
    type: "approval",
    actor: "human",
    label: "Approval",
    x: 70,
    y: 57,
    details: {
      Overview: "Human approval pauses execution until the exact effect is approved or rejected.",
      Policy: "Fingerprint: 8d31a9... Bound to tool name and validated args.",
      Input: "Create Gmail draft. Recipient: self. Subject: Agentic AI content ideas.",
      Effect: "The approved effect is a draft creation only. Gmail send is not implemented.",
    },
  },
  {
    id: "execution",
    type: "execution",
    actor: "runtime",
    label: "Ledger",
    x: 32,
    y: 82,
    details: {
      Overview: "Runtime checks effect ledger before running the approved external write.",
      Trace: "No prior success for this effect key. In-flight ToolCall row created.",
      Metrics: "Execution safety: non_retryable_side_effect.",
    },
  },
  {
    id: "verification",
    type: "verification",
    actor: "runtime",
    label: "Verification",
    x: 52,
    y: 82,
    details: {
      Overview: "Runtime verifies the expected draft effect after tool execution.",
      Output: "Observed Gmail draft id and matching subject.",
      Metrics: "Verification passed in 0.6s.",
    },
  },
  {
    id: "final",
    type: "final",
    actor: "runtime",
    label: "Complete",
    x: 73,
    y: 82,
    details: {
      Overview: "Run completed with verified draft and final answer.",
      Output: "Final answer summarizes evidence, draft status, and next steps.",
      Metrics: "$0.012 estimated, 7.4s excluding approval wait.",
    },
  },
];

const edges = [
  ["goal", "plan"],
  ["plan", "web"],
  ["plan", "memory"],
  ["web", "evidence"],
  ["memory", "evidence"],
  ["evidence", "llm"],
  ["llm", "draft"],
  ["draft", "policy"],
  ["policy", "approval"],
  ["approval", "execution"],
  ["execution", "verification"],
  ["verification", "final"],
];

const initialMessages = [
  {
    role: "user",
    tone: "user",
    text: "Find the top Agentic AI developments today, verify them, and create LinkedIn content ideas.",
  },
  {
    role: "runtime",
    tone: "runtime",
    text: "Run #AF-2841 is ready. The live graph will grow as execution events arrive.",
  },
];

const demoSteps = [
  {
    at: "0.0s",
    node: "goal",
    status: "running",
    label: "Goal accepted",
    meta: "Runtime created run #AF-2841.",
  },
  {
    at: "0.2s",
    node: "plan",
    status: "running",
    complete: ["goal"],
    label: "Planner started",
    meta: "LLM building structured plan.",
    chat: { role: "agent", tone: "agent", text: "I will research current evidence, retrieve relevant memory, synthesize ideas, and gate any external write." },
  },
  {
    at: "1.8s",
    node: "web",
    status: "running",
    complete: ["plan"],
    label: "Web search started",
    meta: "Tool call: web_search.",
  },
  {
    at: "2.1s",
    node: "memory",
    status: "running",
    label: "Memory retrieved",
    meta: "3 selected, 5 dropped.",
    chat: { role: "runtime", tone: "runtime", text: "Memory selected: previous preference for evidence-backed claims and architecture framing." },
  },
  {
    at: "3.4s",
    node: "evidence",
    status: "running",
    complete: ["web", "memory"],
    label: "Evidence grouped",
    meta: "Runtime marked external evidence as untrusted content.",
  },
  {
    at: "4.9s",
    node: "llm",
    status: "running",
    complete: ["evidence"],
    label: "Synthesis started",
    meta: "LLM composing verified ideas.",
  },
  {
    at: "5.6s",
    node: "draft",
    status: "running",
    complete: ["llm"],
    label: "Gmail draft proposed",
    meta: "External write candidate.",
    chat: { role: "agent", tone: "agent", text: "I prepared five LinkedIn content angles and can create a Gmail draft for your review." },
  },
  {
    at: "5.9s",
    node: "policy",
    status: "running",
    complete: ["draft"],
    label: "Policy evaluating",
    meta: "Action type external_write, risk medium.",
  },
  {
    at: "6.0s",
    node: "approval",
    status: "approval_required",
    complete: ["policy"],
    label: "Approval required",
    meta: "Human must approve exact Gmail draft effect.",
    chat: { role: "runtime", tone: "approval", text: "Approval required: create Gmail draft to self with subject 'Agentic AI content ideas'." },
    pause: true,
  },
];

const resumeSteps = [
  {
    at: "12.3s",
    node: "approval",
    status: "succeeded",
    label: "Human approved",
    meta: "Approval bound to unchanged arguments.",
  },
  {
    at: "12.5s",
    node: "execution",
    status: "running",
    label: "Execution ledger checked",
    meta: "No prior successful effect.",
  },
  {
    at: "13.1s",
    node: "verification",
    status: "running",
    complete: ["execution"],
    label: "Verification started",
    meta: "Checking draft id and subject.",
  },
  {
    at: "13.7s",
    node: "final",
    status: "succeeded",
    complete: ["verification"],
    label: "Run completed",
    meta: "Verified draft and final answer ready.",
    chat: { role: "agent", tone: "agent", text: "Done. The draft was created and verified. I included the evidence summary and content angles in the final response." },
  },
];

let state = {
  screen: "mission",
  theme: "dark",
  selectedNodeId: null,
  discovered: new Set(),
  statuses: {},
  timeline: [],
  messages: [...initialMessages],
  timer: null,
  running: false,
  awaitingApproval: false,
  rejected: false,
  viewMode: "studio",
  chatCollapsed: false,
  inspectorCollapsed: false,
  connectionState: "connected",
  approvalArgsChanged: false,
  stepIndex: 0,
  resumeIndex: 0,
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function nodeById(id) {
  return nodes.find((node) => node.id === id);
}

function nodeFamily(node) {
  return visualFamilies[node.type] || "system";
}

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = navGroups
    .map(
      (group) => `
        <div class="nav-group">
          <div class="nav-heading">${group.title}</div>
          ${group.items
            .map(
              (item) => `
                <button class="nav-button ${state.screen === item.id ? "active" : ""}" data-screen="${item.id}" type="button">
                  <span class="nav-icon">${item.icon}</span>
                  <span>${item.label}</span>
                  ${item.badge ? `<span class="nav-badge">${item.badge}</span>` : ""}
                </button>
              `
            )
            .join("")}
        </div>
      `
    )
    .join("");

  nav.querySelectorAll("[data-screen]").forEach((button) => {
    button.addEventListener("click", () => {
      state.screen = button.dataset.screen;
      render();
    });
  });
}

function setHeader() {
  const [eyebrow, title] = screenMeta[state.screen];
  document.getElementById("screen-eyebrow").textContent = eyebrow;
  document.getElementById("screen-title").textContent = title;
  const chip = document.getElementById("run-chip");
  if (state.awaitingApproval) chip.textContent = "Run #AF-2841 awaiting approval";
  else if (state.running) chip.textContent = "Run #AF-2841 running";
  else if (state.rejected) chip.textContent = "Run #AF-2841 rejected";
  else if (state.statuses.final === "succeeded") chip.textContent = "Run #AF-2841 complete";
  else chip.textContent = "Run #AF-2841 idle";
}

function render() {
  setHeader();
  renderNav();
  const root = document.getElementById("screen-root");
  const renderer = screenRenderers[state.screen] || renderMission;
  root.innerHTML = renderer();
  bindScreen();
}

function bindScreen() {
  if (state.screen === "workspace") bindWorkspace();
}

function card(label, value, note, tone = "") {
  return `
    <div class="data-card ${tone}">
      <div class="card-label">${label}</div>
      <div class="card-value">${value}</div>
      <div class="card-note">${note}</div>
    </div>
  `;
}

function panel(title, subtitle, body) {
  return `
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>${title}</h2>
          ${subtitle ? `<div class="panel-subtitle">${subtitle}</div>` : ""}
        </div>
      </div>
      ${body}
    </section>
  `;
}

function rows(items) {
  return `<div class="list">${items
    .map(
      (item) => `
        <div class="list-row">
          <div>
            <div class="row-title">${item.title}</div>
            <div class="row-detail">${item.detail}</div>
          </div>
          <span class="pill ${item.tone || ""}">${item.status}</span>
        </div>
      `
    )
    .join("")}</div>`;
}

function table(headers, data) {
  return `
    <div class="table-panel table-wrap">
      <table>
        <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
        <tbody>
          ${data
            .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`)
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderMission() {
  return `
    <div class="screen-grid mission-grid">
      ${panel(
        "Resume Work",
        "Active runs, blocked work, and recent operator actions.",
        rows([
          { title: "Run #AF-2841", detail: "Gmail draft proposed for Agentic AI content ideas.", status: "Awaiting approval", tone: "approval" },
          { title: "Run #AF-2837", detail: "Investigate failed MCP hiring pipeline query.", status: "Recovered", tone: "success" },
          { title: "Run #AF-2831", detail: "Continue previous AgentForge architecture work using memory.", status: "Complete", tone: "success" },
        ])
      )}
      ${panel(
        "Start A Goal",
        "Representative tasks for demos and manual exploration.",
        rows([
          { title: "Research latest AI developments", detail: "Search, verify, synthesize, and prepare content ideas.", status: "Template", tone: "info" },
          { title: "Prepare recruiter reply", detail: "Search Gmail, understand context, create a draft.", status: "Needs Google", tone: "waiting" },
          { title: "Investigate failed runs", detail: "Open observability metrics and drill into graph nodes.", status: "Ops", tone: "info" },
        ])
      )}
      ${panel(
        "System Pulse",
        "Operational readiness without generic dashboard clutter.",
        `<div class="screen-grid two-col">
          ${card("Active runs", "3", "One waiting for approval.")}
          ${card("Tool health", "17/18", "MCP company_internal needs trust review.")}
          ${card("Eval quality", "0.84", "Last optional quality run.")}
          ${card("Memory updates", "12", "Durable memories written this week.")}
        </div>`
      )}
    </div>
  `;
}

function renderWorkspace() {
  return `
    <div class="workspace view-${state.viewMode} ${state.chatCollapsed ? "chat-collapsed" : ""} ${state.inspectorCollapsed ? "inspector-collapsed" : ""}">
      <div class="workspace-status">
        <div class="status-metrics">
          <span class="metric-chip">Run #AF-2841</span>
          <span class="metric-chip">Status: ${workspaceStatusLabel()}</span>
          <span class="metric-chip">Tokens: ${workspaceTokenLabel()}</span>
          <span class="metric-chip">Cost: ${workspaceCostLabel()}</span>
          <span class="metric-chip">Connection: ${state.connectionState}</span>
        </div>
        <div class="toolbar-actions">
          <button class="secondary-button view-mode ${state.viewMode === "studio" ? "active" : ""}" data-view-mode="studio" type="button">Studio</button>
          <button class="secondary-button view-mode ${state.viewMode === "graph" ? "active" : ""}" data-view-mode="graph" type="button">Graph focus</button>
          <button class="secondary-button view-mode ${state.viewMode === "chat" ? "active" : ""}" data-view-mode="chat" type="button">Chat focus</button>
          <button id="toggle-chat" class="secondary-button" type="button" aria-expanded="${!state.chatCollapsed}">${state.chatCollapsed ? "Show chat" : "Collapse chat"}</button>
          <button id="toggle-inspector" class="secondary-button" type="button" aria-expanded="${!state.inspectorCollapsed}">${state.inspectorCollapsed ? "Show inspector" : "Collapse inspector"}</button>
          <button id="simulate-reconnect" class="secondary-button" type="button">Reconnect</button>
          <button id="run-demo" class="primary-button" type="button">Run Demo</button>
          <button id="reset-demo" class="secondary-button" type="button">Reset</button>
        </div>
      </div>

      <div class="workspace-body">
        <section class="chat-panel" aria-label="Chat">
          <div class="panel-toolbar">
            <h2>Chat</h2>
            <span class="pill ${state.awaitingApproval ? "approval" : state.running ? "running" : "info"}">${workspaceStatusLabel()}</span>
          </div>
          <div id="chat-stream" class="chat-stream">${renderMessages()}</div>
          <form id="chat-form" class="chat-composer">
            <input id="chat-input" aria-label="Message" value="Compare Langfuse and DeepEval using current evidence." />
            <button class="secondary-button" type="submit">Send</button>
          </form>
        </section>

        <section class="graph-panel" aria-label="Live execution graph">
          <div class="panel-toolbar">
            <h2>Live Execution Graph</h2>
            <div class="toolbar-actions">
              <button id="select-current" class="secondary-button" type="button">Current</button>
              <button id="fit-view" class="secondary-button" type="button">Fit</button>
            </div>
          </div>
          <div class="graph-legend" aria-label="Execution actors">
            <span><i class="actor-swatch llm"></i> LLM proposes</span>
            <span><i class="actor-swatch runtime"></i> Runtime enforces</span>
            <span><i class="actor-swatch tool"></i> Tool performs</span>
            <span><i class="actor-swatch memory"></i> Memory/knowledge informs</span>
            <span><i class="actor-swatch human"></i> Human authorizes</span>
          </div>
          <div id="graph-canvas" class="graph-canvas">${renderGraph()}</div>
        </section>

        <section class="inspector-panel" aria-label="Inspector">
          <div class="panel-toolbar">
            <h2>Inspector</h2>
            ${state.selectedNodeId ? `<span class="pill info">${nodeById(state.selectedNodeId).type}</span>` : ""}
          </div>
          <div id="inspector" class="inspector-body">${renderInspector()}</div>
        </section>
      </div>

      <section class="timeline-panel" aria-label="Run timeline">
        <div class="panel-toolbar">
          <h2>Run Timeline</h2>
          <span class="pill info">${state.timeline.length} events</span>
        </div>
        <div id="timeline-list" class="timeline-list">${renderTimeline()}</div>
      </section>
    </div>
  `;
}

function workspaceStatusLabel() {
  if (state.awaitingApproval) return "approval required";
  if (state.rejected) return "rejected";
  if (state.statuses.final === "succeeded") return "completed";
  if (state.running) return "running";
  return "idle";
}

function workspaceTokenLabel() {
  if (state.statuses.final === "succeeded") return "8,930";
  if (state.timeline.length > 0) return "5,780";
  return "0";
}

function workspaceCostLabel() {
  if (state.statuses.final === "succeeded") return "$0.012";
  if (state.timeline.length > 0) return "$0.007";
  return "$0.000";
}

function renderMessages() {
  return state.messages
    .map(
      (message) => `
        <article class="message ${message.tone}">
          <div class="message-role">${message.role}</div>
          <div class="message-text">${escapeHtml(message.text)}</div>
        </article>
      `
    )
    .join("");
}

function visibleNodes() {
  return nodes.filter((node) => state.discovered.has(node.id));
}

function renderGraph() {
  const nodeSet = new Set(visibleNodes().map((node) => node.id));
  const svgEdges = edges
    .filter(([from, to]) => nodeSet.has(from) && nodeSet.has(to))
    .map(([from, to]) => {
      const a = nodeById(from);
      const b = nodeById(to);
      const edgeStatus = edgeClass(from, to);
      return `<line class="edge-line ${edgeStatus}" x1="${a.x}%" y1="${a.y}%" x2="${b.x}%" y2="${b.y}%" />`;
    })
    .join("");

  const nodeHtml = visibleNodes()
    .map((node) => {
      const status = state.statuses[node.id] || "pending";
      const selected = state.selectedNodeId === node.id ? "selected" : "";
      const family = nodeFamily(node);
      return `
        <button class="exec-node actor-${node.actor} family-${family} ${status} ${selected}" data-node="${node.id}" style="left:${node.x}%;top:${node.y}%;" type="button" aria-label="${node.label}, ${familyLabels[family]}, ${statusLabel(status)}">
          <div class="node-topline">
            <span class="node-type">${familyLabels[family]} / ${node.type}</span>
            <span class="actor-dot" aria-hidden="true"></span>
          </div>
          <div class="node-title">${node.label}</div>
          <div class="node-status">${statusLabel(status)}</div>
        </button>
      `;
    })
    .join("");

  const empty = state.discovered.size === 0 ? `<div class="empty-note" style="padding:16px;">No runtime events yet. Start the demo to grow the execution graph.</div>` : "";
  return `<svg class="edge-layer" aria-hidden="true">${svgEdges}</svg>${empty}${nodeHtml}`;
}

function edgeClass(from, to) {
  const fromStatus = state.statuses[from];
  const toStatus = state.statuses[to];
  if (toStatus === "running" || toStatus === "approval_required") return "active";
  if (fromStatus === "failed" || toStatus === "failed" || toStatus === "blocked") return "failed";
  if (fromStatus === "succeeded" && toStatus === "succeeded") return "success";
  return "";
}

function statusLabel(status) {
  const labels = {
    pending: "pending",
    running: "running",
    succeeded: "succeeded",
    failed: "failed",
    blocked: "blocked",
    approval_required: "approval required",
  };
  return labels[status] || status;
}

function renderInspector() {
  const node = state.selectedNodeId ? nodeById(state.selectedNodeId) : null;
  if (!node) {
    return `<p class="empty-note">Select a graph node or timeline event to inspect inputs, outputs, policy, memory, evidence, context, and metrics.</p>`;
  }

  const status = state.statuses[node.id] || "pending";
  const tabs = Object.keys(node.details);
  const detailRows = Object.entries(node.details)
    .map(
      ([label, value]) => `
        <div class="detail-row">
          <div class="detail-label">${label}</div>
          <div class="detail-value">${escapeHtml(value)}</div>
        </div>
      `
    )
    .join("");

  const approval = state.awaitingApproval && node.id === "approval" ? renderApprovalBox() : "";
  const context = node.id === "llm" ? renderContextInspector() : "";

  return `
    ${approval}
    <div class="inspector-tabs">${tabs.map((tab) => `<span class="tab-pill">${tab}</span>`).join("")}</div>
    <div class="detail-grid">
      <div class="detail-row">
        <div class="detail-label">Node</div>
        <div class="detail-value">${node.label} / ${familyLabels[nodeFamily(node)]} / ${node.actor}</div>
      </div>
      <div class="detail-row">
        <div class="detail-label">Status</div>
        <div class="detail-value">${statusLabel(status)}</div>
      </div>
      ${detailRows}
    </div>
    ${context}
  `;
}

function renderContextInspector() {
  const rows = [
    ["System", 16, "2,100"],
    ["Conversation", 29, "3,800"],
    ["Summary", 9, "1,200"],
    ["Memory", 7, "940"],
    ["Tool results", 17, "2,300"],
    ["RAG", 24, "3,100"],
    ["Reserved", 31, "4,000"],
  ];
  return `
    <div class="detail-row" style="margin-top:14px;">
      <div class="detail-label">Context Window</div>
      <div class="detail-value">72% used. 4,000 tokens reserved for output. 5,900 tokens avoided through summary, truncation, and dropped optional context. Safe runtime metadata only.</div>
    </div>
    <div class="context-bars" style="margin-top:10px;">
      ${rows
        .map(
          ([label, pct, value]) => `
            <div class="context-row">
              <span>${label}</span>
              <div class="bar"><span style="width:${pct}%;"></span></div>
              <span>${value}</span>
            </div>
          `
        )
        .join("")}
    </div>
    <div class="context-decisions">
      <div>
        <div class="detail-label">Included</div>
        <div class="detail-value">Pinned decision memory, relevance .91. Recent conversation summary. Three RAG chunks above cutoff.</div>
      </div>
      <div>
        <div class="detail-label">Dropped</div>
        <div class="detail-value">Five memory candidates dropped for low relevance, one superseded item dropped, long tool output compressed under budget pressure.</div>
      </div>
    </div>
  `;
}

function renderApprovalBox() {
  const disabled = state.approvalArgsChanged ? "disabled" : "";
  const warning = state.approvalArgsChanged
    ? `<div class="approval-warning">Arguments changed - new approval required before execution can resume.</div>`
    : "";
  return `
    <div class="approval-box">
      <h3>Approval Required</h3>
      <div class="card-note">AgentForge wants to create one Gmail draft. Policy: REQUIRE_APPROVAL. Action: EXTERNAL_WRITE. Risk: MEDIUM.</div>
      ${warning}
      <div class="detail-grid" style="margin-top:10px;">
        <div class="detail-row">
          <div class="detail-label">Tool</div>
          <div class="detail-value">gmail_create_draft</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">Exact effect</div>
          <div class="detail-value">Create Gmail draft only. No email will be sent.</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">To / Subject</div>
          <div class="detail-value">self / Agentic AI content ideas</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">Body preview</div>
          <div class="detail-value">Five LinkedIn angles with verified source notes and a short evidence summary.</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">Argument fingerprint</div>
          <div class="detail-value">8d31a9b0c4e2. Bound to tool name and validated args.</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">Expires</div>
          <div class="detail-value">15 minutes after request.</div>
        </div>
      </div>
      <div class="approval-actions">
        <button id="approve-demo" class="primary-button" type="button" ${disabled}>Approve</button>
        <button id="reject-demo" class="danger-button" type="button">Reject</button>
        <button id="change-args-demo" class="secondary-button" type="button">Simulate changed args</button>
      </div>
    </div>
  `;
}

function renderTimeline() {
  if (state.timeline.length === 0) {
    return `<div class="empty-note">Timeline is empty until runtime events arrive.</div>`;
  }
  return state.timeline
    .map(
      (event, index) => `
        <button class="timeline-event ${state.selectedNodeId === event.node ? "active" : ""}" data-timeline-index="${index}" type="button">
          <div class="timeline-time">${event.at}</div>
          <div class="timeline-label">${event.label}</div>
          <div class="timeline-meta">${event.meta}</div>
        </button>
      `
    )
    .join("");
}

function bindWorkspace() {
  const run = document.getElementById("run-demo");
  const reset = document.getElementById("reset-demo");
  const current = document.getElementById("select-current");
  const fit = document.getElementById("fit-view");
  const form = document.getElementById("chat-form");

  run.addEventListener("click", startDemo);
  reset.addEventListener("click", resetDemo);
  document.querySelectorAll("[data-view-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.viewMode = button.dataset.viewMode;
      rerenderWorkspaceOnly();
    });
  });
  document.getElementById("toggle-chat")?.addEventListener("click", () => {
    state.chatCollapsed = !state.chatCollapsed;
    rerenderWorkspaceOnly();
  });
  document.getElementById("toggle-inspector")?.addEventListener("click", () => {
    state.inspectorCollapsed = !state.inspectorCollapsed;
    rerenderWorkspaceOnly();
  });
  document.getElementById("simulate-reconnect")?.addEventListener("click", simulateReconnect);
  current.addEventListener("click", selectCurrentNode);
  fit.addEventListener("click", () => {
    state.messages.push({ role: "runtime", tone: "runtime", text: "Graph recentered on the current execution path." });
    rerenderWorkspaceOnly();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.getElementById("chat-input");
    const text = input.value.trim();
    if (!text) return;
    state.messages.push({ role: "user", tone: "user", text });
    state.messages.push({ role: "runtime", tone: "runtime", text: "Message attached to the current run conversation." });
    input.value = "";
    rerenderWorkspaceOnly();
  });

  document.querySelectorAll("[data-node]").forEach((nodeButton) => {
    nodeButton.addEventListener("click", () => {
      state.selectedNodeId = nodeButton.dataset.node;
      rerenderWorkspaceOnly();
    });
  });
  document.querySelectorAll("[data-timeline-index]").forEach((eventButton) => {
    eventButton.addEventListener("click", () => {
      const event = state.timeline[Number(eventButton.dataset.timelineIndex)];
      state.selectedNodeId = event.node;
      rerenderWorkspaceOnly();
    });
  });
  document.getElementById("approve-demo")?.addEventListener("click", approveDemo);
  document.getElementById("reject-demo")?.addEventListener("click", rejectDemo);
  document.getElementById("change-args-demo")?.addEventListener("click", simulateChangedArgs);
  scrollChatAndTimeline();
}

function rerenderWorkspaceOnly() {
  if (state.screen !== "workspace") return render();
  document.getElementById("screen-root").innerHTML = renderWorkspace();
  setHeader();
  bindWorkspace();
}

function clearTimer() {
  if (state.timer) window.clearTimeout(state.timer);
  state.timer = null;
}

function resetDemo() {
  clearTimer();
  state.discovered = new Set();
  state.statuses = {};
  state.timeline = [];
  state.messages = [...initialMessages];
  state.selectedNodeId = null;
  state.running = false;
  state.awaitingApproval = false;
  state.rejected = false;
  state.connectionState = "connected";
  state.approvalArgsChanged = false;
  state.stepIndex = 0;
  state.resumeIndex = 0;
  render();
}

function startDemo() {
  resetDemo();
  state.running = true;
  state.messages.push({ role: "runtime", tone: "runtime", text: "Demo run started. Events will update chat, graph, timeline, and inspector together." });
  state.screen = "workspace";
  render();
  scheduleNextDemoStep();
}

function scheduleNextDemoStep() {
  clearTimer();
  state.timer = window.setTimeout(() => {
    const step = demoSteps[state.stepIndex];
    if (!step) {
      state.running = false;
      rerenderWorkspaceOnly();
      return;
    }
    applyStep(step);
    state.stepIndex += 1;
    rerenderWorkspaceOnly();
    if (step.pause) {
      state.awaitingApproval = true;
      state.running = false;
      rerenderWorkspaceOnly();
      return;
    }
    scheduleNextDemoStep();
  }, state.stepIndex === 0 ? 200 : 820);
}

function applyStep(step) {
  state.discovered.add(step.node);
  state.statuses[step.node] = step.status;
  if (step.complete) {
    step.complete.forEach((nodeId) => {
      state.discovered.add(nodeId);
      state.statuses[nodeId] = "succeeded";
    });
  }
  state.selectedNodeId = step.node;
  state.timeline.push({ at: step.at, node: step.node, label: step.label, meta: step.meta });
  if (step.chat) state.messages.push(step.chat);
}

function approveDemo() {
  if (!state.awaitingApproval) return;
  if (state.approvalArgsChanged) return;
  state.awaitingApproval = false;
  state.running = true;
  state.messages.push({ role: "runtime", tone: "runtime", text: "Approval recorded. Execution resumed with the unchanged approved arguments." });
  state.resumeIndex = 0;
  rerenderWorkspaceOnly();
  scheduleResumeStep();
}

function simulateChangedArgs() {
  if (!state.awaitingApproval) return;
  state.approvalArgsChanged = true;
  state.timeline.push({
    at: "6.2s",
    node: "approval",
    label: "Arguments changed",
    meta: "Approved fingerprint invalidated; fresh approval required.",
  });
  state.messages.push({ role: "runtime", tone: "approval", text: "Arguments changed after the approval request. The previous approval is invalid and a new request is required." });
  rerenderWorkspaceOnly();
}

function simulateReconnect() {
  state.connectionState = "reconnecting";
  state.messages.push({ role: "runtime", tone: "runtime", text: "SSE disconnected. Keeping current run state visible while refetching durable records." });
  rerenderWorkspaceOnly();
  window.setTimeout(() => {
    state.connectionState = "connected";
    state.timeline.push({
      at: state.timeline.length ? "snapshot" : "0.0s",
      node: state.selectedNodeId || "goal",
      label: "Recovered from snapshot",
      meta: "Durable task, trace, and approval state rebuilt after reconnect.",
    });
    state.messages.push({ role: "runtime", tone: "runtime", text: "Reconnected. Graph and timeline rebuilt from durable run state; SSE resumes as live narration." });
    rerenderWorkspaceOnly();
  }, 900);
}

function scheduleResumeStep() {
  clearTimer();
  state.timer = window.setTimeout(() => {
    const step = resumeSteps[state.resumeIndex];
    if (!step) {
      state.running = false;
      rerenderWorkspaceOnly();
      return;
    }
    applyStep(step);
    state.resumeIndex += 1;
    if (step.node === "final") state.running = false;
    rerenderWorkspaceOnly();
    if (state.running) scheduleResumeStep();
  }, 780);
}

function rejectDemo() {
  if (!state.awaitingApproval) return;
  clearTimer();
  state.awaitingApproval = false;
  state.running = false;
  state.rejected = true;
  state.statuses.approval = "failed";
  state.discovered.add("final");
  state.statuses.final = "blocked";
  state.selectedNodeId = "approval";
  state.timeline.push({
    at: "12.3s",
    node: "approval",
    label: "Human rejected",
    meta: "External write not authorized. Run stopped before Gmail execution.",
  });
  state.messages.push({ role: "runtime", tone: "approval", text: "Approval rejected. The Gmail draft was not created and the run stopped safely." });
  rerenderWorkspaceOnly();
}

function selectCurrentNode() {
  const last = state.timeline[state.timeline.length - 1];
  if (last) state.selectedNodeId = last.node;
  rerenderWorkspaceOnly();
}

function scrollChatAndTimeline() {
  const chat = document.getElementById("chat-stream");
  if (chat) chat.scrollTop = chat.scrollHeight;
  const timeline = document.getElementById("timeline-list");
  if (timeline) timeline.scrollLeft = timeline.scrollWidth;
}

function renderRuns() {
  return `
    <div class="screen-grid">
      ${table(
        ["Goal", "Status", "Started", "Duration", "Steps", "Tools", "Tokens", "Cost", "Approvals"],
        [
          ["Agentic AI developments and LinkedIn ideas", `<span class="pill approval">Awaiting approval</span>`, "Today 10:42", "6.0s + wait", "8", "web_search, gmail_create_draft", "5,780", "$0.007", "1 pending"],
          ["Investigate failed MCP hiring pipeline", `<span class="pill success">Recovered</span>`, "Today 09:18", "38.4s", "14", "mcp.company_internal", "12,420", "$0.031", "0"],
          ["Continue AgentForge architecture work", `<span class="pill success">Completed</span>`, "Yesterday", "21.7s", "9", "memory, file_io", "9,810", "$0.018", "0"],
          ["Compare Langfuse and DeepEval", `<span class="pill failed">Failed</span>`, "Yesterday", "12.2s", "5", "web_search", "4,100", "$0.009", "0"],
        ]
      )}
    </div>
  `;
}

function renderKnowledge() {
  return `
    <div class="screen-grid two-col">
      ${panel(
        "Sources",
        "Ingestion and indexing state.",
        `<div class="source-grid">
          ${card("Drive", "Connected", "42 docs indexed.")}
          ${card("Uploads", "18", "3 documents reindexed today.")}
          ${card("Internal", "Healthy", "MCP source reachable.")}
          ${card("Failures", "1", "One PDF extraction warning.")}
        </div>`
      )}
      ${panel(
        "Retrieval Playground",
        "Debug what RAG can retrieve before an agent run.",
        `<div class="query-box">
          <input value="hiring workflow escalation policy" aria-label="Retrieval query" />
          <button class="secondary-button" type="button">Test</button>
        </div>
        ${rows([
          { title: "onboarding_guide.md / chunk 14", detail: "Score 0.86. Mentions recruiter follow-up and role context.", status: "Selected", tone: "success" },
          { title: "incident_response_runbook.md / chunk 3", detail: "Score 0.71. Related escalation flow but weak domain fit.", status: "Dropped", tone: "waiting" },
          { title: "api_design_guidelines.md / chunk 9", detail: "Score 0.64. Below default cutoff.", status: "Dropped", tone: "" },
        ])}`
      )}
    </div>
  `;
}

function renderMemory() {
  return `
    <div class="screen-grid">
      <div class="query-box">
        <input value="AgentForge architecture preferences" aria-label="Search memory" />
        <button class="secondary-button" type="button">Search</button>
      </div>
      <div class="screen-grid three-col">
        ${card("Rolling summary", "Task-local", "Lossy current conversation summary, not durable memory.")}
        ${card("Durable memory", "148", "Cross-run semantic, episodic, pinned_decision, preference, and artifact_reference entries.")}
        ${card("Context-selected", "3", "Memories selected for the current model call; dropped candidates are inspectable.")}
      </div>
      ${table(
        ["Kind", "Memory", "Importance", "Provenance", "Last used", "Status"],
        [
          ["Preference", "Prefer evidence-backed architecture notes before frontend rewrites.", "5", "Run #AF-2764", "Today", `<span class="pill success">Active</span>`],
          ["Decision", "Phase 8 keeps Postgres as durable truth and SSE as live narration.", "5", "Phase 8 note", "Today", `<span class="pill success">Active</span>`],
          ["Episodic", "Prior UI prototype was rejected because only one screen was usable.", "4", "Run #AF-2809", "Yesterday", `<span class="pill info">Pinned</span>`],
          ["Fact", "Gmail action currently creates drafts; send is not implemented.", "5", "Tool registry", "Today", `<span class="pill success">Active</span>`],
        ]
      )}
    </div>
  `;
}

function renderTools() {
  return `
    <div class="screen-grid three-col">
      ${panel("Web", "", rows([
        { title: "web_search", detail: "Current research over web providers. READ / medium.", status: "Available", tone: "success" },
        { title: "knowledge_search", detail: "Hybrid RAG query through rag-api.", status: "Available", tone: "success" },
      ]))}
      ${panel("Google", "", rows([
        { title: "gmail_search", detail: "Search/read connected mailbox snippets.", status: "Available", tone: "success" },
        { title: "gmail_create_draft", detail: "External write. Approval required.", status: "Approval", tone: "approval" },
        { title: "google_drive_search", detail: "Read Drive files under connected account.", status: "Available", tone: "success" },
      ]))}
      ${panel("Execution", "", rows([
        { title: "file_io", detail: "Reads/writes task workspace. Write requires approval.", status: "Mixed", tone: "waiting" },
        { title: "code_execution", detail: "Runs bounded container execution.", status: "Approval", tone: "approval" },
        { title: "delegate_subagent", detail: "Bounded nested tool-use loop.", status: "Available", tone: "success" },
      ]))}
    </div>
  `;
}

function renderMcp() {
  return `
    <div class="screen-grid two-col">
      ${panel(
        "Configured Servers",
        "Trust and discovery state.",
        rows([
          { title: "company_internal", detail: "stdio transport. 4 tools discovered. Last discovery 17 minutes ago.", status: "Trust review", tone: "approval" },
          { title: "analytics_local", detail: "stdio transport. Schema unchanged.", status: "Trusted", tone: "success" },
        ])
      )}
      ${panel(
        "Schema Review",
        "Changed MCP schemas fail closed for trust-sensitive classifications.",
        table(
          ["Tool", "Fingerprint", "Reviewed as", "Current schema", "State"],
          [
            ["company_internal.update_candidate", "f91c2d8e10ab", "EXTERNAL_WRITE / HIGH", "New input field detected", `<span class="pill approval">Review required</span>`],
            ["company_internal.pipeline_metrics", "a7b4e18cd553", "READ / LOW", "Unchanged", `<span class="pill success">Reviewed</span>`],
            ["analytics_local.query", "dd810ac44b21", "READ / MEDIUM", "Unchanged", `<span class="pill success">Reviewed</span>`],
          ]
        )
      )}
    </div>
  `;
}

function renderIntegrations() {
  return `
    <div class="screen-grid three-col">
      ${panel("Gmail", "Connected as akshay@example.com.", rows([
        { title: "Search/read", detail: "Search recruiter conversations and read snippets.", status: "Granted", tone: "success" },
        { title: "Create drafts", detail: "Draft creation requires policy approval per call.", status: "Granted", tone: "approval" },
        { title: "Gmail send tool", detail: "Not implemented in AgentForge; draft creation is the only Gmail write shown.", status: "Unavailable", tone: "" },
      ]))}
      ${panel("Google Drive", "Connected through the same Google grant.", rows([
        { title: "Search files", detail: "Read metadata and retrieve file text.", status: "Granted", tone: "success" },
        { title: "Write files", detail: "No Drive write tool exposed.", status: "Unavailable", tone: "" },
      ]))}
      ${panel("Google Photos", "Optional provider.", rows([
        { title: "Search media", detail: "Read-only photo discovery where configured.", status: "Needs consent", tone: "waiting" },
      ]))}
    </div>
  `;
}

function renderApprovals() {
  return `
    <div class="screen-grid two-col">
      ${panel(
        "Pending Approval",
        "Exact effect authorization.",
        `<div class="approval-box">
          <h3>Create Gmail draft</h3>
          <div class="card-note">Tool: gmail_create_draft. Recipient: self. Subject: Agentic AI content ideas. Body preview: five LinkedIn angles with verified source notes. Risk: EXTERNAL_WRITE / MEDIUM. Policy: REQUIRE_APPROVAL. Expires in 15m.</div>
          <div class="approval-actions">
            <button class="primary-button" type="button">Approve</button>
            <button class="danger-button" type="button">Reject</button>
          </div>
        </div>
        ${rows([
          { title: "Arguments", detail: "tool=gmail_create_draft, fingerprint=8d31a9b0c4e2.", status: "Unchanged", tone: "success" },
          { title: "Changed args example", detail: "If recipient, subject, or body changes, this approval becomes invalid and a fresh approval is required.", status: "Invalidates", tone: "failed" },
          { title: "Policy reason", detail: "External write changes a service outside AgentForge.", status: "Required", tone: "approval" },
        ])}`
      )}
      ${panel(
        "Approval History",
        "Recent human-in-the-loop decisions.",
        rows([
          { title: "Run #AF-2829", detail: "file_io write report.md approved by human.", status: "Approved", tone: "success" },
          { title: "Run #AF-2821", detail: "code_execution request rejected after inspecting args.", status: "Rejected", tone: "failed" },
        ])
      )}
    </div>
  `;
}

function renderPolicies() {
  return `
    <div class="screen-grid two-col">
      ${panel(
        "Policy Explorer",
        "Read-only deterministic policy surface.",
        table(
          ["Classification", "Decision", "Reason"],
          [
            ["READ / LOW or MEDIUM", `<span class="pill success">ALLOW</span>`, "Ordinary read guarded by tool boundary."],
            ["LOCAL_WRITE", `<span class="pill approval">REQUIRE APPROVAL</span>`, "Writes to task workspace."],
            ["EXTERNAL_WRITE", `<span class="pill approval">REQUIRE APPROVAL</span>`, "Changes service outside AgentForge."],
            ["DESTRUCTIVE", `<span class="pill failed">DENY</span>`, "No safe destructive execution semantics."],
            ["CRITICAL", `<span class="pill failed">DENY</span>`, "No rule permits critical risk."],
          ]
        )
      )}
      ${panel(
        "Recent Decisions",
        "Per-call policy outcomes.",
        rows([
          { title: "gmail_create_draft", detail: "external_write / medium.", status: "Require approval", tone: "approval" },
          { title: "web_search", detail: "read / medium.", status: "Allow", tone: "success" },
          { title: "file_io write", detail: "argument-dependent local_write.", status: "Require approval", tone: "approval" },
        ])
      )}
    </div>
  `;
}

function renderEvals() {
  return `
    <div class="screen-grid two-col">
      ${panel("Deterministic Safety", "Release-blocking checks from pytest and the agent battery.", rows([
        { title: "Tier 1 pytest invariants", detail: "Policy, approval binding, execution ledger, guardrails, auth.", status: "Passed", tone: "success" },
        { title: "Tier 2 agent battery", detail: "Golden workflows over real runs.", status: "Passed", tone: "success" },
      ]))}
      ${panel("Probabilistic Quality", "Judge-model scores are trend signals, not runtime safety.", rows([
        { title: "DeepEval optional run", detail: "Answer relevance and faithfulness when a judge model is configured.", status: "0.84", tone: "info" },
        { title: "Skipped state", detail: "Quality run reports unavailable when judge dependencies or keys are missing.", status: "Explicit", tone: "waiting" },
      ]))}
      ${panel("Datasets", "Representative cases.", rows([
        { title: "agent_golden_tasks.json", detail: "Web, RAG, Gmail draft, MCP, memory continuation.", status: "18 cases", tone: "info" },
        { title: "security_redteam_tasks.json", detail: "Prompt injection, MCP poisoning, approval bypass, secrets.", status: "13 categories", tone: "info" },
        { title: "quality_golden_tasks.json", detail: "Optional DeepEval quality cases.", status: "Ready", tone: "success" },
      ]))}
    </div>
  `;
}

function renderObservability() {
  return `
    <div class="screen-grid two-col">
      ${panel(
        "Agent Metrics",
        "Operational signals from AgentForge records.",
        `<div class="mini-chart">
          ${chartRow("Task latency p50", 38, "18.4s")}
          ${chartRow("LLM latency p95", 64, "4.8s")}
          ${chartRow("Tool latency p95", 47, "3.1s")}
          ${chartRow("Approval wait p50", 72, "9m")}
          ${chartRow("Cached tokens", 28, "18%")}
        </div>`
      )}
      ${panel("Drill-down", "Metric to run to graph node.", rows([
        { title: "MCP timeout spike", detail: "5 failures on company_internal.hiring_pipeline.", status: "Investigate", tone: "failed" },
        { title: "Recovery events", detail: "2 stranded tasks re-enqueued after worker restart.", status: "Recovered", tone: "success" },
        { title: "Ambiguous effects", detail: "0 unresolved external-write effects.", status: "Clear", tone: "success" },
        { title: "Policy denies", detail: "Click a denial to open run detail, selected policy node, and trace span.", status: "Drill in", tone: "info" },
      ]))}
    </div>
  `;
}

function chartRow(label, width, value) {
  return `
    <div class="chart-row">
      <span>${label}</span>
      <div class="bar"><span style="width:${width}%;"></span></div>
      <span>${value}</span>
    </div>
  `;
}

function renderSecurity() {
  return `
    <div class="screen-grid two-col">
      ${panel("Guardrail Events", "Safe metadata only.", rows([
        { title: "Input guard", detail: "Risk type approval_bypass. Payload redacted. Stage: pre-plan.", status: "Blocked", tone: "failed" },
        { title: "Retrieval guard", detail: "External document flagged for prompt injection. Content preview redacted.", status: "Flagged", tone: "waiting" },
        { title: "Secret detector", detail: "Credential-like string blocked from model output. Secret value redacted.", status: "Blocked", tone: "failed" },
        { title: "Output guard", detail: "No credential disclosure detected in last 24h.", status: "Clear", tone: "success" },
      ]))}
      ${panel("MCP Trust", "Schema and description risk.", rows([
        { title: "company_internal", detail: "Tool schema changed since last review.", status: "Review required", tone: "approval" },
        { title: "analytics_local", detail: "Fingerprint unchanged and description scan clean.", status: "Trusted", tone: "success" },
      ]))}
    </div>
  `;
}

function renderSettings() {
  return `
    <div class="screen-grid two-col">
      ${panel("Runtime Configuration", "Read-only until backend mutation exists.", rows([
        { title: "Agent model", detail: "Configured through environment.", status: "Read-only", tone: "info" },
        { title: "Max task steps", detail: "Runtime limit enforced by backend.", status: "Read-only", tone: "info" },
        { title: "Memory enabled", detail: "Durable memory retrieval and completion reflection.", status: "Enabled", tone: "success" },
        { title: "Context budget", detail: "Model-window metadata and reserved output budget.", status: "Measured", tone: "success" },
      ]))}
      ${panel("User Preferences", "Prototype-only local state.", rows([
        { title: "Theme", detail: "Dark and light mode both supported in this prototype.", status: state.theme, tone: "info" },
        { title: "Default workspace", detail: "Three-column studio with graph focus available later.", status: "Layout A", tone: "success" },
      ]))}
    </div>
  `;
}

const screenRenderers = {
  mission: renderMission,
  workspace: renderWorkspace,
  runs: renderRuns,
  knowledge: renderKnowledge,
  memory: renderMemory,
  tools: renderTools,
  mcp: renderMcp,
  integrations: renderIntegrations,
  approvals: renderApprovals,
  policies: renderPolicies,
  evals: renderEvals,
  observability: renderObservability,
  security: renderSecurity,
  settings: renderSettings,
};

document.getElementById("theme-toggle").addEventListener("click", () => {
  state.theme = state.theme === "dark" ? "light" : "dark";
  document.body.classList.toggle("light", state.theme === "light");
  render();
});

render();
