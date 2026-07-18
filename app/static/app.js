const canvas = document.querySelector("#castle");
const context = canvas.getContext("2d");
const eventsList = document.querySelector("#events");
const stateBadge = document.querySelector("#trust-state");
const selection = document.querySelector("#selection");
const gateControls = document.querySelector("#gate-controls");
const approvalCopy = document.querySelector("#approval-copy");
const queryForm = document.querySelector("#query-form");
const queryResult = document.querySelector("#query-result");

let events = [];
let activeApproval = null;
let currentState = "NORMAL";
let lastEventId = 0;

function drawPixelRect(x, y, width, height, color) {
  context.fillStyle = color;
  context.fillRect(Math.round(x), Math.round(y), Math.round(width), Math.round(height));
}

function renderCastle() {
  const width = canvas.width;
  const height = canvas.height;
  drawPixelRect(0, 0, width, height, "#263d62");
  drawPixelRect(0, height * 0.72, width, height * 0.28, "#213a29");
  drawPixelRect(width * 0.1, height * 0.14, width * 0.8, height * 0.6, "#8b8fa2");
  drawPixelRect(width * 0.11, height * 0.06, width * 0.12, height * 0.68, "#a6a9b8");
  drawPixelRect(width * 0.77, height * 0.06, width * 0.12, height * 0.68, "#a6a9b8");
  drawPixelRect(width * 0.38, height * 0.22, width * 0.24, height * 0.52, "#a6a9b8");
  drawPixelRect(width * 0.46, height * 0.54, width * 0.08, height * 0.2, currentState === "LOCKED" ? "#461d24" : "#392d28");
  drawPixelRect(width * 0.1, height * 0.02, width * 0.79, 7, currentState === "LOCKED" ? "#ff7474" : "#82e8a2");

  const latestByItem = new Map();
  for (const event of events) {
    if (event.source_item_id) latestByItem.set(event.source_item_id, event);
  }
  [...latestByItem.values()].slice(-8).forEach((event, index) => {
    const x = 40 + index * 105;
    const y = height * 0.68 - (index % 2) * 38;
    const type = event.type;
    const hostile = type === "incident_created" || event.trust_state === "LOCKED";
    const held = type === "approval_created" || event.trust_state === "RESTRICTED";
    drawPixelRect(x, y, 14, 20, hostile ? "#d84343" : held ? "#ffc86b" : "#d7efff");
    drawPixelRect(x - 3, y - 7, 20, 8, hostile ? "#7f1f2b" : "#24476e");
    if (hostile) {
      drawPixelRect(width * 0.18, height * 0.22, 65, 4, "#ffd66b");
    }
  });
}

function updateState(state) {
  if (!state) return;
  currentState = state;
  stateBadge.textContent = state;
  stateBadge.className = `state ${state}`;
}

function showEvent(event) {
  lastEventId = Math.max(lastEventId, event.id || 0);
  events.push(event);
  updateState(event.trust_state);
  const item = document.createElement("li");
  item.textContent = `#${event.id ?? "?"} ${event.type}${event.source_item_id ? ` — ${event.source_item_id}` : ""}`;
  item.addEventListener("click", () => {
    selection.textContent = JSON.stringify(event, null, 2);
  });
  eventsList.prepend(item);
  if (event.type === "approval_created") {
    activeApproval = event.payload.approval_id;
    approvalCopy.textContent = `Approve or deny ${event.payload.action}.`;
    gateControls.hidden = false;
  }
  if (event.type === "approval_resolved") {
    gateControls.hidden = true;
    activeApproval = null;
  }
  renderCastle();
}

async function loadEvents() {
  const response = await fetch(`/api/events?after_id=${lastEventId}`);
  const payload = await response.json();
  payload.forEach(showEvent);
}

async function connect() {
  await loadEvents();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/events?after_id=${lastEventId}`);
  socket.addEventListener("message", ({ data }) => showEvent(JSON.parse(data)));
  socket.addEventListener("close", () => setTimeout(connect, 1500));
}

async function resolveApproval(status) {
  if (!activeApproval) return;
  const response = await fetch(`/api/approvals/${activeApproval}/${status}`, { method: "POST" });
  if (!response.ok) selection.textContent = await response.text();
}

async function loadFixtures() {
  const response = await fetch("/api/demo/fixtures");
  const fixtures = await response.json();
  for (const fixture of fixtures) {
    const button = document.createElement("button");
    button.textContent = fixture.risk.replace("_", " ");
    button.addEventListener("click", async () => {
      await fetch(`/api/demo/fixtures/${fixture.id}/inject`, { method: "POST" });
    });
    document.querySelector("#fixtures").append(button);
  }
}

queryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.querySelector("#query").value;
  const response = await fetch("/api/intelligence/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  queryResult.textContent = JSON.stringify(await response.json(), null, 2);
});
document.querySelector("#approve").addEventListener("click", () => resolveApproval("approve"));
document.querySelector("#deny").addEventListener("click", () => resolveApproval("deny"));

renderCastle();
loadFixtures();
connect();
