import type { Evidence, Fixture, SceneSnapshot, TowerEvent } from "./types";

export class ApiClient {
  private csrfToken: string | null = null;

  async login(token: string): Promise<void> {
    const response = await this.request("/api/operator/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const payload = (await response.json()) as { csrf_token: string };
    this.csrfToken = payload.csrf_token;
  }

  async restoreSession(): Promise<boolean> {
    const response = await fetch("/api/operator/session", { credentials: "same-origin" });
    if (!response.ok) return false;
    const payload = (await response.json()) as { csrf_token: string };
    this.csrfToken = payload.csrf_token;
    return true;
  }

  async logout(): Promise<void> {
    await this.mutate("/api/operator/logout");
    this.csrfToken = null;
  }

  async snapshot(): Promise<SceneSnapshot> {
    return this.json<SceneSnapshot>("/api/scene");
  }

  async events(afterId: number): Promise<TowerEvent[]> {
    return this.json<TowerEvent[]>(`/api/events?after_id=${afterId}&limit=500`);
  }

  async fixtures(): Promise<Fixture[]> {
    return this.json<Fixture[]>("/api/demo/fixtures");
  }

  async evidence(sourceItemId: string): Promise<Evidence> {
    return this.json<Evidence>(`/api/evidence/${encodeURIComponent(sourceItemId)}`);
  }

  async mutate(path: string, body?: unknown): Promise<Response> {
    if (!this.csrfToken) throw new Error("Operator session required");
    return this.request(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-csrf-token": this.csrfToken,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  async query(question: string): Promise<Record<string, unknown>> {
    const response = await this.request("/api/intelligence/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: question }),
    });
    return (await response.json()) as Record<string, unknown>;
  }

  private async json<T>(path: string): Promise<T> {
    const response = await this.request(path);
    return (await response.json()) as T;
  }

  private async request(path: string, init?: RequestInit): Promise<Response> {
    const response = await fetch(path, { ...init, credentials: "same-origin" });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `${response.status} ${response.statusText}`);
    }
    return response;
  }
}

export class EventConnection {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private stopped = false;
  private cursor = 0;

  constructor(
    private readonly onEvent: (event: TowerEvent) => void,
    private readonly onStatus: (connected: boolean) => void,
  ) {}

  connect(afterId: number): void {
    this.stopped = false;
    this.cursor = Math.max(this.cursor, afterId);
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(
      `${protocol}://${location.host}/ws/events?after_id=${this.cursor}`,
    );
    this.socket.addEventListener("open", () => this.onStatus(true));
    this.socket.addEventListener("message", ({ data }) => {
      const event = JSON.parse(String(data)) as TowerEvent;
      this.cursor = Math.max(this.cursor, event.id);
      this.onEvent(event);
    });
    this.socket.addEventListener("close", () => {
      this.onStatus(false);
      if (!this.stopped) {
        this.reconnectTimer = window.setTimeout(() => this.connect(this.cursor), 1500);
      }
    });
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.socket?.close();
  }
}
