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
}
