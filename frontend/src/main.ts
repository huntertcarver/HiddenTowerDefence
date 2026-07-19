import Phaser from "phaser";

import { ApiClient, EventConnection } from "./api";
import "./styles.css";
import { TowerScene } from "./TowerScene";
import type { Approval, SceneSnapshot, TowerEvent } from "./types";

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
const operatorDialog = document.querySelector<HTMLDialogElement>("#operator-dialog");
const operatorButton = requiredElement("#operator-button");
const loginForm = document.querySelector<HTMLFormElement>("#operator-login");
const loginError = requiredElement("#login-error");
const fixtureList = requiredElement("#fixture-list");
const queryForm = document.querySelector<HTMLFormElement>("#query-form");
const queryResult = requiredElement("#query-result");

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
    cursor = snapshot.cursor;
    for (const event of await api.events(Math.max(snapshot.cursor - 100, 0))) {
      handleEvent(event);
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

function handleEvent(event: TowerEvent): void {
  if (seenEvents.has(event.id)) return;
  seenEvents.add(event.id);
  if (seenEvents.size > 1000) {
    const first = seenEvents.values().next().value as number | undefined;
    if (first !== undefined) seenEvents.delete(first);
  }
  cursor = Math.max(cursor, event.id);
  towerScene.applyEvent(event);
  const entityId = event.entity_id ?? event.source_item_id;
  if (entityId) {
    const history = eventsByEntity.get(entityId) ?? [];
    history.push(event);
    eventsByEntity.set(entityId, history.slice(-100));
  }
  prependConsoleEvent(event);
  if (
    ["approval_created", "approval_resolved", "incident_created", "incident_resolved"].includes(
      event.type,
    )
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
  detailPanel.innerHTML = `<h3>${escapeText(entityId)}</h3>`;
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
