export type TrustState = "NORMAL" | "RESTRICTED" | "LOCKED";

export interface TowerEvent {
  schema_version: number;
  id: number;
  type: string;
  source_item_id: string | null;
  run_id: string | null;
  entity_id: string | null;
  correlation_id: string | null;
  trust_state: TrustState | null;
  payload: Record<string, unknown>;
  occurred_at: string;
}

export interface Approval {
  id: string;
  source_item_id: string;
  action: string;
  status: string;
}

export interface Incident {
  id: string;
  source_item_id: string;
  severity: string;
  summary: string;
  status: string;
}

export interface SceneSnapshot {
  schema_version: number;
  cursor: number;
  trust_state: TrustState;
  approvals: Approval[];
  incidents: Incident[];
  active_items: Array<{
    id: string;
    simulated: boolean;
    processing_status: string;
  }>;
  demo: Record<string, unknown>;
}

export interface Fixture {
  id: string;
  risk: string;
  title: string;
}

export interface EntityState {
  id: string;
  kind: "traveler" | "citizen" | "restricted" | "enemy" | "worker" | "messenger";
  eventIds: number[];
  simulated: boolean;
  title?: string;
}

/** Payload dispatched by the game scene when the pointer enters an entity. */
export interface EntityHoverDetail {
  entityId: string | null;
  kind: EntityState["kind"] | "guard";
  simulated: boolean;
  title: string | null;
  x: number;
  y: number;
}

export interface ScanSummary {
  boundary: string;
  action: string;
  threat_level: string;
  detected: boolean;
}

export interface Evidence {
  source: {
    id: string;
    title: string;
    text: string;
    comments: string[];
    url?: string | null;
    author?: string | null;
    score?: number | null;
    comment_count?: number | null;
    processing_status: string;
    source: string;
    simulated: boolean;
  };
  scans: ScanSummary[];
  triage: {
    summary: string;
    category: string;
    priority: string;
    sentiment: string;
    topics: string[];
    recommended_action: string;
    rationale: string;
  } | null;
  decision: {
    status: string;
    summary: string;
    latest_action: string;
    latest_threat_level: string;
  };
  scope: string;
}
