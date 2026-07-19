import Phaser from "phaser";

import { ApiClient, EventConnection } from "./api";
import "./styles.css";
import { TowerScene } from "./TowerScene";
import type {
  Approval,
  EntityHoverDetail,
  Evidence,
  SceneSnapshot,
  TowerEvent,
} from "./types";

const api = new ApiClient();
const towerScene = new TowerScene();
const eventsByEntity = new Map<string, TowerEvent[]>();
const seenEvents = new Set<number>();
let cursor = 0;
let selectedEntityId: string | null = null;
let operatorReady = false;
let game: Phaser.Game | null = null;

const trustBadge = requiredElement("#trust-state");
const connectionBadge = requiredElement("#connection-state");
const consoleList = requiredElement("#console-list");
const detailPanel = requiredElement("#entity-details");
const approvalPanel = requiredElement("#approval-panel");
const incidentPanel = requiredElement("#incident-panel");
const operatorDialog = document.querySelector<HTMLDialogElement>("#operator-dialog");
const operatorButton = requiredElement("#operator-button");
const loginForm = document.querySelector<HTMLFormElement>("#operator-login");
const loginError = requiredElement("#login-error");
const fixtureList = requiredElement("#fixture-list");
const queryForm = document.querySelector<HTMLFormElement>("#query-form");
const queryResult = requiredElement("#query-result");
const tooltip = requiredElement("#entity-tooltip");

async function bootstrap(): Promise<void> {
  const [snapshot, fixtures, session] = await Promise.all([
    api.snapshot(),
    api.fixtures(),
    api.restoreSession().catch(() => false),
  ]);
  operatorReady = session;
  updateOperatorUi();
  renderFixtures(fixtures);
  towerScene.onReady(async () => {
    towerScene.applySnapshot(snapshot);
    renderApprovals(snapshot.approvals);
    renderIncidentControls(snapshot);
    cursor = snapshot.cursor;
    for (const event of await api.events(Math.max(snapshot.cursor - 100, 0))) {
      handleEvent(event, false);
    }
    const connection = new EventConnection(handleEvent, updateConnection);
    connection.connect(cursor);
  });
  game = new Phaser.Game({
    type: Phaser.AUTO,
    parent: "game-root",
    backgroundColor: "#15263a",
    pixelArt: true,
    roundPixels: true,
    physics: { default: "arcade" },
    scale: {
      mode: Phaser.Scale.RESIZE,
      autoCenter: Phaser.Scale.CENTER_BOTH,
      width: window.innerWidth,
      height: window.innerHeight,
    },
    scene: towerScene,
  });
}

function handleEvent(event: TowerEvent, animate = true): void {
  if (seenEvents.has(event.id)) return;
  seenEvents.add(event.id);
  if (seenEvents.size > 1000) {
    const first = seenEvents.values().next().value as number | undefined;
    if (first !== undefined) seenEvents.delete(first);
  }
  cursor = Math.max(cursor, event.id);
  if (animate) towerScene.applyEvent(event);
  const entityId = event.entity_id ?? event.source_item_id;
  if (entityId) {
    const history = eventsByEntity.get(entityId) ?? [];
    history.push(event);
    eventsByEntity.set(entityId, history.slice(-100));
  }
  prependConsoleEvent(event);
  if (
    [
      "approval_created",
      "approval_resolved",
      "incident_created",
      "incident_acknowledged",
      "incident_resolved",
    ].includes(event.type)
  ) {
    refreshSnapshot().catch(showError);
  }
  if (selectedEntityId && selectedEntityId === entityId) renderEntityDetails(entityId);
}

function prependConsoleEvent(event: TowerEvent): void {
  const item = document.createElement("button");
  item.className = `console-entry event-${event.type.replaceAll("_", "-")}`;
  item.dataset.entityId = event.entity_id ?? event.source_item_id ?? "";
  const time = new Date(event.occurred_at).toLocaleTimeString();
  const boundary = event.payload.boundary ? ` · ${String(event.payload.boundary)}` : "";
  const action = event.payload.action ? ` · ${String(event.payload.action)}` : "";
  const simulated = event.payload.simulated ? " · SIMULATED" : "";
  item.innerHTML = `
    <span class="console-time">${escapeText(time)}</span>
    <strong>${escapeText(event.type)}</strong>
    <span>${escapeText(event.source_item_id ?? event.run_id ?? "runtime")}${escapeText(
      boundary + action + simulated,
    )}</span>
  `;
  item.addEventListener("click", () => {
    const entityId = item.dataset.entityId;
    if (!entityId) return;
    selectedEntityId = entityId;
    renderEntityDetails(entityId);
    window.dispatchEvent(new CustomEvent("tower-focus-entity", { detail: entityId }));
  });
  consoleList.prepend(item);
  while (consoleList.children.length > 300) consoleList.lastElementChild?.remove();
}

function renderEntityDetails(entityId: string): void {
  const history = eventsByEntity.get(entityId) ?? [];
  detailPanel.innerHTML = `<h3>Loading source details…</h3>`;
  evidenceFor(entityId)
    .then((evidence) => {
      const triage = evidence.triage;
      const sourceLink = evidence.source.url
        ? `<a href="${escapeText(evidence.source.url)}" target="_blank" rel="noopener">Open source article ↗</a>`
        : "";
      const comments = evidence.source.comments
        .slice(0, 2)
        .map((comment) => `<li>${escapeText(comment.slice(0, 240))}</li>`)
        .join("");
      detailPanel.innerHTML = `
        <h3>${escapeText(evidence.source.title)}</h3>
        <p class="decision-summary">${escapeText(evidence.decision.summary)}</p>
        <dl class="classification-grid">
          <dt>Decision</dt><dd>${escapeText(evidence.decision.latest_action)}</dd>
          <dt>Threat</dt><dd>${escapeText(evidence.decision.latest_threat_level)}</dd>
          <dt>Category</dt><dd>${escapeText(triage?.category ?? "Not classified")}</dd>
          <dt>Priority</dt><dd>${escapeText(triage?.priority ?? "Pending")}</dd>
          <dt>Sentiment</dt><dd>${escapeText(triage?.sentiment ?? "Pending")}</dd>
          <dt>Action</dt><dd>${escapeText(triage?.recommended_action ?? "Pending")}</dd>
        </dl>
        ${sourceLink}
        <p>${escapeText((triage?.summary || evidence.source.text || "No excerpt available").slice(0, 500))}</p>
        ${triage?.rationale ? `<p><strong>Why:</strong> ${escapeText(triage.rationale)}</p>` : ""}
        ${comments ? `<h4>Source comments</h4><ul>${comments}</ul>` : ""}
        <h4>Processing history</h4>
      `;
      appendEventHistory(history);
    })
    .catch(() => {
      detailPanel.innerHTML = `<h3>${escapeText(entityId)}</h3><p>Source details are unavailable.</p><h4>Processing history</h4>`;
      appendEventHistory(history);
    });
}

function appendEventHistory(history: TowerEvent[]): void {
  if (!history.length) {
    detailPanel.insertAdjacentHTML("beforeend", "<p>No recent event history.</p>");
    return;
  }
  const list = document.createElement("ol");
  for (const event of history) {
    const item = document.createElement("li");
    item.textContent = `#${event.id} ${event.type}${
      event.payload.boundary ? ` · ${String(event.payload.boundary)}` : ""
    }`;
    list.append(item);
  }
  detailPanel.append(list);
}

async function refreshSnapshot(): Promise<void> {
  const snapshot = await api.snapshot();
  towerScene.applySnapshot(snapshot);
  renderApprovals(snapshot.approvals);
  renderIncidentControls(snapshot);
}

function renderApprovals(approvals: Approval[]): void {
  approvalPanel.replaceChildren();
  if (!approvals.length) {
    approvalPanel.innerHTML = "<p>No travelers awaiting a decision.</p>";
    return;
  }
  for (const approval of approvals) {
    const card = document.createElement("article");
    card.className = "approval-card";
    card.innerHTML = `
      <strong>${escapeText(approval.action)}</strong>
      <span>${escapeText(approval.source_item_id)}</span>
    `;
    const approve = document.createElement("button");
    approve.textContent = "Approve";
    approve.addEventListener("click", () =>
      operatorMutation(`/api/approvals/${approval.id}/approve`),
    );
    const deny = document.createElement("button");
    deny.className = "danger";
    deny.textContent = "Deny";
    deny.addEventListener("click", () =>
      operatorMutation(`/api/approvals/${approval.id}/deny`),
    );
    card.append(approve, deny);
    approvalPanel.append(card);
  }
}

function renderIncidentControls(snapshot: SceneSnapshot): void {
  incidentPanel.replaceChildren();
  const demoRunning = snapshot.demo.running === true;
  if (demoRunning) {
    const stopDemo = document.createElement("button");
    stopDemo.className = "secondary";
    stopDemo.textContent = "Stop demo";
    stopDemo.addEventListener("click", () => operatorMutation("/api/demo/stop"));
    incidentPanel.append(stopDemo);
  }
  if (snapshot.incidents.length || snapshot.trust_state !== "NORMAL") {
    const resetAll = document.createElement("button");
    resetAll.className = "danger";
    resetAll.textContent = "Clear all & restart demo";
    resetAll.addEventListener("click", () => {
      if (
        window.confirm(
          "Resolve all incidents and taints, return to NORMAL, and restart the demo?",
        )
      ) {
        void operatorMutation("/api/incidents/resolve-all", {
          resolution: "Bulk operator reset from the game UI",
          restart_demo: true,
        });
      }
    });
    incidentPanel.append(resetAll);
  }
  for (const incident of snapshot.incidents) {
    const card = document.createElement("article");
    card.className = "incident-card";
    card.innerHTML = `
      <strong>${escapeText(incident.severity)} incident</strong>
      <span>${escapeText(incident.source_item_id)}</span>
      <p>${escapeText(incident.summary)}</p>
    `;
    if (incident.status === "open") {
      const acknowledge = document.createElement("button");
      acknowledge.textContent = "Acknowledge";
      acknowledge.addEventListener("click", () =>
        operatorMutation(`/api/incidents/${incident.id}/acknowledge`),
      );
      card.append(acknowledge);
    } else {
      const resolve = document.createElement("button");
      resolve.textContent = "Resolve";
      resolve.addEventListener("click", () => {
        const resolution = window.prompt(
          "Resolution reason",
          "Reviewed and resolved by the operator",
        );
        if (resolution?.trim()) {
          void operatorMutation(`/api/incidents/${incident.id}/resolve`, {
            resolution: resolution.trim(),
          });
        }
      });
      card.append(resolve);
    }
    incidentPanel.append(card);
  }
  if (!snapshot.incidents.length) {
    const empty = document.createElement("p");
    empty.textContent = "No active incidents.";
    incidentPanel.append(empty);
  }
  if (snapshot.trust_state === "RESTRICTED" && !snapshot.incidents.length) {
    const resume = document.createElement("button");
    resume.textContent = "Resume normal";
    resume.addEventListener("click", () => operatorMutation("/api/state/resume"));
    incidentPanel.append(resume);
  }
}

function renderFixtures(fixtures: Awaited<ReturnType<ApiClient["fixtures"]>>): void {
  fixtureList.replaceChildren();
  for (const fixture of fixtures) {
    const button = document.createElement("button");
    button.textContent = fixture.risk.replaceAll("_", " ");
    button.title = fixture.title;
    button.addEventListener("click", () =>
      operatorMutation(`/api/demo/fixtures/${fixture.id}/inject`),
    );
    fixtureList.append(button);
  }
}

async function operatorMutation(path: string, body?: unknown): Promise<void> {
  if (!operatorReady) {
    operatorDialog?.showModal();
    return;
  }
  try {
    await api.mutate(path, body);
    await refreshSnapshot();
  } catch (error) {
    showError(error);
  }
}

function updateOperatorUi(): void {
  operatorButton.textContent = operatorReady ? "Operator online" : "Operator login";
  operatorButton.classList.toggle("online", operatorReady);
  document.body.classList.toggle("operator-authenticated", operatorReady);
}

function updateConnection(connected: boolean): void {
  connectionBadge.textContent = connected ? "LIVE" : "RECONNECTING";
  connectionBadge.className = connected ? "connection live" : "connection offline";
}

// ------------------------------------------------------------ hover tooltip

const ROLE_INFO: Record<string, { name: string; blurb: string }> = {
  traveler: {
    name: "Incoming content",
    blurb: "A new item approaching the gate for its first security scan.",
  },
  citizen: {
    name: "Clean content",
    blurb: "Passed every scan and was admitted into the castle.",
  },
  restricted: {
    name: "Awaiting decision",
    blurb: "Flagged as suspicious. Held at the gate until an operator approves or denies it.",
  },
  enemy: {
    name: "Blocked threat",
    blurb: "High-severity detection. The agent is locked and this content never reaches the model.",
  },
  guard: {
    name: "Gate guard",
    blurb: "Every traveler is scanned by HiddenLayer at this checkpoint.",
  },
  worker: {
    name: "Tool run",
    blurb: "The model requested a controlled tool. Arguments and results are scanned too.",
  },
  messenger: {
    name: "Report delivery",
    blurb: "Carrying the model's triage summary out of the keep.",
  },
};

const evidenceCache = new Map<string, Promise<Evidence>>();
let hoveredEntityId: string | null = null;

function evidenceFor(entityId: string): Promise<Evidence> {
  const sourceId = entityId.replace(/:(worker|messenger)$/, "");
  let pending = evidenceCache.get(sourceId);
  if (!pending) {
    pending = api.evidence(sourceId);
    evidenceCache.set(sourceId, pending);
    pending.catch(() => evidenceCache.delete(sourceId));
  }
  return pending;
}

function verdictFromScans(scans: Evidence["scans"]): { text: string; tone: string } {
  if (scans.some((scan) => scan.action.toLowerCase() === "block")) {
    return { text: "BLOCKED", tone: "locked" };
  }
  if (scans.some((scan) => scan.detected || scan.action.toLowerCase() === "alert")) {
    return { text: "FLAGGED", tone: "restricted" };
  }
  return { text: "CLEAN", tone: "normal" };
}

function positionTooltip(x: number, y: number): void {
  const width = tooltip.offsetWidth || 300;
  const height = tooltip.offsetHeight || 160;
  const left = Math.min(x + 18, window.innerWidth - width - 12);
  const top = Math.min(y + 18, window.innerHeight - height - 12);
  tooltip.style.left = `${Math.max(12, left)}px`;
  tooltip.style.top = `${Math.max(12, top)}px`;
}

function renderTooltip(detail: EntityHoverDetail, evidence: Evidence | null): void {
  const role = ROLE_INFO[detail.kind] ?? { name: detail.kind, blurb: "" };
  const parts: string[] = [];
  parts.push(`
    <header class="tooltip-header kind-${escapeText(detail.kind)}">
      <strong>${escapeText(role.name)}</strong>
      ${detail.simulated || evidence?.source.simulated ? '<span class="tag simulated">SIMULATED</span>' : ""}
    </header>
  `);
  if (evidence) {
    const verdict = verdictFromScans(evidence.scans);
    const sourceName =
      evidence.source.source === "fixture" ? "Demo fixture" : "Hacker News";
    const excerpt = evidence.source.text.trim().slice(0, 180);
    parts.push(`
      <p class="tooltip-title">${escapeText(evidence.source.title)}</p>
      <p class="tooltip-meta">
        <span class="tag">${escapeText(sourceName)}</span>
        <span class="tag verdict-${verdict.tone}">${verdict.text}</span>
      </p>
      ${excerpt ? `<p class="tooltip-excerpt">${escapeText(excerpt)}${evidence.source.text.length > 180 ? "…" : ""}</p>` : ""}
    `);
    if (evidence.scans.length) {
      const rows = evidence.scans
        .map(
          (scan) => `
            <li>
              <span>${escapeText(scan.boundary)}</span>
              <span class="scan-${scan.detected ? "flagged" : "clear"}">
                ${escapeText(scan.action)}${scan.threat_level && scan.threat_level !== "None" ? ` · ${escapeText(scan.threat_level)}` : ""}
              </span>
            </li>
          `,
        )
        .join("");
      parts.push(`<ul class="tooltip-scans">${rows}</ul>`);
    }
    parts.push('<p class="tooltip-hint">Click to pin full history in the side panel</p>');
  } else if (detail.entityId) {
    parts.push(`<p class="tooltip-excerpt">${escapeText(role.blurb)}</p>`);
    parts.push('<p class="tooltip-hint">Loading content…</p>');
  } else {
    parts.push(`<p class="tooltip-excerpt">${escapeText(role.blurb)}</p>`);
  }
  tooltip.innerHTML = parts.join("");
}

window.addEventListener("tower-entity-hover", (event) => {
  const detail = (event as CustomEvent<EntityHoverDetail | null>).detail;
  if (!detail) {
    hoveredEntityId = null;
    tooltip.classList.remove("visible");
    return;
  }
  tooltip.classList.add("visible");
  positionTooltip(detail.x, detail.y);
  const hoverKey = detail.entityId ?? `role:${detail.kind}`;
  if (hoveredEntityId === hoverKey) return;
  hoveredEntityId = hoverKey;
  renderTooltip(detail, null);
  if (!detail.entityId) return;
  evidenceFor(detail.entityId)
    .then((evidence) => {
      if (hoveredEntityId !== hoverKey) return;
      renderTooltip(detail, evidence);
      positionTooltip(detail.x, detail.y);
    })
    .catch(() => {
      if (hoveredEntityId !== hoverKey) return;
      renderTooltip(detail, null);
    });
});

window.addEventListener("tower-entity-selected", (event) => {
  selectedEntityId = (event as CustomEvent<string>).detail;
  renderEntityDetails(selectedEntityId);
});

window.addEventListener("tower-trust-state", (event) => {
  const state = (event as CustomEvent<string>).detail;
  trustBadge.textContent = state;
  trustBadge.className = `trust-state ${state.toLowerCase()}`;
});

operatorButton.addEventListener("click", async () => {
  if (operatorReady) {
    await api.logout();
    operatorReady = false;
    updateOperatorUi();
    return;
  }
  operatorDialog?.showModal();
});

loginForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const token = new FormData(loginForm).get("token");
  try {
    await api.login(String(token ?? ""));
    operatorReady = true;
    updateOperatorUi();
    operatorDialog?.close();
    loginForm.reset();
  } catch (error) {
    loginError.textContent = error instanceof Error ? error.message : "Login failed";
  }
});

document.querySelector("#dialog-cancel")?.addEventListener("click", () => {
  operatorDialog?.close();
});
document.querySelector("#console-toggle")?.addEventListener("click", () => {
  document.querySelector("#console")?.classList.toggle("collapsed");
});
document.querySelector("#demo-start")?.addEventListener("click", () =>
  operatorMutation("/api/demo/start"),
);
document.querySelector("#demo-stop")?.addEventListener("click", () =>
  operatorMutation("/api/demo/stop"),
);
document.querySelector("#demo-reset")?.addEventListener("click", () =>
  operatorMutation("/api/demo/reset"),
);
document.querySelector("#clear-scene")?.addEventListener("click", () => {
  towerScene.clearEntities();
  eventsByEntity.clear();
  selectedEntityId = null;
  detailPanel.innerHTML = "<p>Scene cleared. New arrivals will appear one at a time.</p>";
});
document.querySelector("#apify-run-now")?.addEventListener("click", () =>
  operatorMutation("/api/apify/run"),
);

queryForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = new FormData(queryForm).get("query");
  queryResult.textContent = "Claw is reviewing deterministic evidence…";
  try {
    const result = await api.query(String(question ?? ""));
    queryResult.textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    queryResult.textContent = error instanceof Error ? error.message : "Query failed";
  }
});

function requiredElement(selector: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(selector);
  if (!element) throw new Error(`Missing required element: ${selector}`);
  return element;
}

function escapeText(value: string): string {
  const element = document.createElement("span");
  element.textContent = value;
  return element.innerHTML;
}

function showError(error: unknown): void {
  detailPanel.textContent = error instanceof Error ? error.message : String(error);
}

void bootstrap().catch(showError);
