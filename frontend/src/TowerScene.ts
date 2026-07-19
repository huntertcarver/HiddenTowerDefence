import Phaser from "phaser";

import {
  createProceduralPixelAssets,
  KIND_COLORS,
  PALETTE,
  seededRandom,
} from "./assets";
import type { EntityHoverDetail, EntityState, SceneSnapshot, TowerEvent, TrustState } from "./types";

interface EntityRecord {
  state: EntityState;
  container: Phaser.GameObjects.Container;
  sprite: Phaser.GameObjects.Sprite;
  label: Phaser.GameObjects.Text;
}

const WORLD_WIDTH = 1600;
const WORLD_HEIGHT = 900;
const GATE_X = 575;
const GATE_Y = 552;
const KEEP_X = 800;
const QUARANTINE = { x: 1180, y: 715 };
const WORKSHOP = { x: 1005, y: 470 };

const ROAD_POINTS = [
  [0, 690], [565, 535], [1010, 535], [1600, 770],
  [1600, 880], [1010, 620], [565, 620], [0, 785],
] as const;

export class TowerScene extends Phaser.Scene {
  private readonly entities = new Map<string, EntityRecord>();
  private readonly processedEventIds = new Set<number>();
  private portcullis!: Phaser.GameObjects.TileSprite;
  private beacon!: Phaser.GameObjects.Arc;
  private stateLabel!: Phaser.GameObjects.Text;
  private keepGlow!: Phaser.GameObjects.Rectangle;
  private trustState: TrustState = "NORMAL";
  private ready = false;
  private readonly readyCallbacks: Array<() => void> = [];

  constructor() {
    super("tower");
  }

  create(): void {
    createProceduralPixelAssets(this);
    this.cameras.main.setBackgroundColor(PALETTE.water);
    this.cameras.main.setBounds(0, 0, WORLD_WIDTH, WORLD_HEIGHT);
    this.physics.world.setBounds(0, 0, WORLD_WIDTH, WORLD_HEIGHT);
    this.drawWorld();
    this.scale.on("resize", () => this.fitCamera());
    this.fitCamera();
    window.addEventListener("tower-focus-entity", (event) => {
      this.focusEntity((event as CustomEvent<string>).detail);
    });
    this.ready = true;
    for (const callback of this.readyCallbacks.splice(0)) callback();
  }

  onReady(callback: () => void): void {
    if (this.ready) callback();
    else this.readyCallbacks.push(callback);
  }

  applySnapshot(snapshot: SceneSnapshot): void {
    this.setTrustState(snapshot.trust_state);
    for (const item of snapshot.active_items) {
      this.ensureEntity(item.id, "traveler", item.simulated);
    }
    for (const approval of snapshot.approvals) {
      this.ensureEntity(approval.source_item_id, "restricted", false);
    }
    for (const incident of snapshot.incidents) {
      this.ensureEntity(incident.source_item_id, "enemy", false);
    }
  }

  applyEvent(event: TowerEvent): void {
    if (this.processedEventIds.has(event.id)) return;
    this.processedEventIds.add(event.id);
    if (this.processedEventIds.size > 5000) {
      const first = this.processedEventIds.values().next().value as number | undefined;
      if (first !== undefined) this.processedEventIds.delete(first);
    }
    if (event.trust_state) this.setTrustState(event.trust_state);
    const entityId = event.entity_id ?? event.source_item_id;
    if (entityId) this.recordEvent(entityId, event);

    switch (event.type) {
      case "content_received":
        if (entityId) {
          this.spawnTraveler(entityId, Boolean(event.payload.simulated));
          if (typeof event.payload.title === "string") {
            this.setEntityTitle(entityId, event.payload.title);
          }
        }
        break;
      case "scan_started":
        if (entityId) this.inspect(entityId);
        break;
      case "scan_completed":
        if (
          entityId &&
          event.payload.boundary === "ingest" &&
          event.payload.action === "Allow"
        ) {
          this.admitCitizen(entityId);
        }
        break;
      case "model_started":
        this.activateKeep();
        break;
      case "model_completed":
        if (entityId) this.launchMessenger(entityId);
        break;
      case "tool_requested":
        if (entityId) this.launchWorker(entityId);
        break;
      case "tool_completed":
        if (entityId) this.returnWorker(entityId);
        break;
      case "approval_created":
        if (entityId) this.detain(entityId);
        break;
      case "approval_resolved":
        if (entityId) {
          if (event.payload.status === "approved") this.admitCitizen(entityId);
          else this.quarantine(entityId);
        }
        break;
      case "detection":
        if (
          entityId &&
          ["High", "Critical"].includes(String(event.payload.threat_level))
        ) {
          this.makeEnemy(entityId);
        }
        break;
      case "state_changed":
        this.setTrustState(String(event.payload.to) as TrustState);
        break;
      case "incident_created":
        if (entityId) this.fireCrossbows(entityId);
        break;
      case "heartbeat":
        this.pulseBeacon();
        break;
      default:
        break;
    }
  }

  focusEntity(entityId: string): void {
    const record = this.entities.get(entityId);
    if (!record) return;
    this.cameras.main.pan(record.container.x, record.container.y, 450, "Sine.easeInOut");
    this.tweens.add({
      targets: record.container,
      alpha: 0.35,
      yoyo: true,
      repeat: 3,
      duration: 120,
    });
  }

  clearEntities(): void {
    for (const record of this.entities.values()) {
      this.tweens.killTweensOf(record.container);
      this.tweens.killTweensOf(record.sprite);
      record.container.destroy(true);
    }
    this.entities.clear();
  }

  setEntityTitle(entityId: string, title: string): void {
    const record = this.entities.get(entityId);
    if (!record) return;
    record.state.title = title;
    record.label.setText(truncate(title, 22));
  }

  // ------------------------------------------------------------------ world

  private drawWorld(): void {
    this.add
      .tileSprite(WORLD_WIDTH / 2, WORLD_HEIGHT / 2, WORLD_WIDTH, WORLD_HEIGHT, "grass")
      .setTileScale(2);
    this.scatterGrassDetail();
    this.drawRoad();
    this.scatterTrees();
    this.drawCastle();
  }

  private scatterGrassDetail(): void {
    const random = seededRandom(97);
    for (let index = 0; index < 130; index += 1) {
      const x = random() * WORLD_WIDTH;
      const y = random() * WORLD_HEIGHT;
      this.add
        .image(x, y, "grass2")
        .setScale(2)
        .setAlpha(0.9);
    }
  }

  private drawRoad(): void {
    const graphics = this.add.graphics();
    graphics.fillStyle(PALETTE.roadEdge, 1);
    graphics.fillPoints(
      ROAD_POINTS.map(([x, y]) => new Phaser.Math.Vector2(x, y - 20)),
      true,
    );
    graphics.fillStyle(PALETTE.road, 1);
    const inner = [
      [0, 710], [575, 555], [1000, 555], [1600, 790],
      [1600, 845], [1000, 595], [575, 595], [0, 760],
    ];
    const polygon = new Phaser.Geom.Polygon(
      inner.map(([x, y]) => new Phaser.Math.Vector2(x, y)),
    );
    graphics.fillPoints(
      inner.map(([x, y]) => new Phaser.Math.Vector2(x, y)),
      true,
    );
    const random = seededRandom(53);
    graphics.fillStyle(PALETTE.roadDark, 0.8);
    for (let index = 0; index < 320; index += 1) {
      const x = random() * WORLD_WIDTH;
      const y = 500 + random() * 400;
      if (!polygon.contains(x, y)) continue;
      graphics.fillRect(x, y, 3 + random() * 3, 2);
    }
  }

  private scatterTrees(): void {
    const random = seededRandom(23);
    const castle = new Phaser.Geom.Rectangle(360, 60, 890, 590);
    const quarantineArea = new Phaser.Geom.Rectangle(1050, 610, 260, 210);
    const road = new Phaser.Geom.Polygon(
      ROAD_POINTS.map(([x, y]) => new Phaser.Math.Vector2(x, y)),
    );
    let placed = 0;
    while (placed < 26) {
      const x = random() * (WORLD_WIDTH - 60) + 30;
      const y = random() * (WORLD_HEIGHT - 80) + 30;
      if (
        castle.contains(x, y) ||
        quarantineArea.contains(x, y) ||
        road.contains(x, y) ||
        road.contains(x, y + 40)
      ) {
        continue;
      }
      this.add
        .image(x, y, "tree")
        .setScale(3)
        .setDepth(y);
      placed += 1;
    }
  }

  private drawCastle(): void {
    const graphics = this.add.graphics();

    // Courtyard.
    this.add
      .tileSprite(800, 410, 510, 290, "grass2")
      .setTileScale(2);

    // Curtain walls built from brick tiles.
    const wall = (x: number, y: number, width: number, height: number): void => {
      this.add.tileSprite(x + width / 2, y + height / 2, width, height, "brick").setTileScale(2);
      graphics.lineStyle(3, 0x2f3644, 1);
      graphics.strokeRect(x, y, width, height);
    };
    wall(450, 170, 700, 95);   // north
    wall(450, 265, 95, 315);   // west
    wall(1055, 265, 95, 315);  // east
    wall(450, 545, 700, 50);   // south (thin, gate splits it visually)

    // Shaded courtyard edge so the interior reads as a separate space.
    graphics.fillStyle(0x1e4a2c, 0.55);
    graphics.fillRect(545, 265, 510, 10);
    graphics.fillRect(545, 265, 10, 280);
    graphics.fillRect(1045, 265, 10, 280);

    // Crenellations along the north wall.
    graphics.fillStyle(PALETTE.stoneLight, 1);
    for (let x = 455; x < 1145; x += 34) graphics.fillRect(x, 160, 20, 12);
    graphics.fillStyle(PALETTE.stoneDark, 1);
    for (let x = 455; x < 1145; x += 34) graphics.fillRect(x, 170, 20, 3);

    // Corner towers with conical roofs and banners.
    for (const [x, y] of [[430, 140], [1070, 140], [430, 485], [1070, 485]]) {
      this.add.tileSprite(x + 50, y + 60, 100, 120, "brick").setTileScale(2);
      graphics.fillStyle(PALETTE.stoneDark, 1);
      graphics.fillRect(x, y - 6, 100, 8);
      graphics.fillStyle(PALETTE.roofDark, 1);
      graphics.fillTriangle(x - 8, y - 4, x + 50, y - 52, x + 108, y - 4);
      graphics.fillStyle(PALETTE.roof, 1);
      graphics.fillTriangle(x, y - 4, x + 50, y - 46, x + 100, y - 4);
      this.add.image(x + 50, y - 62, "banner").setScale(2).setOrigin(0.5, 0);
    }

    // The keep, where the Nemotron model reviews content. Tinted warmer and
    // outlined so it reads as its own building inside the courtyard.
    this.add
      .tileSprite(KEEP_X, 355, 230, 175, "brick")
      .setTileScale(2)
      .setTint(0xd9c7ae);
    const keepArt = this.add.graphics();
    keepArt.lineStyle(4, 0x3b332a, 1);
    keepArt.strokeRect(KEEP_X - 115, 268, 230, 175);
    keepArt.fillStyle(PALETTE.roofDark, 1);
    keepArt.fillTriangle(KEEP_X - 130, 270, KEEP_X, 190, KEEP_X + 130, 270);
    keepArt.fillStyle(PALETTE.roof, 1);
    keepArt.fillTriangle(KEEP_X - 114, 266, KEEP_X, 200, KEEP_X + 114, 266);
    this.add.image(KEEP_X, 176, "banner").setScale(2.4).setOrigin(0.5, 0);
    // Windows that glow while the model is thinking.
    this.keepGlow = this.add.rectangle(KEEP_X, 350, 180, 110, 0x4da3ff, 0.06);
    for (const [wx, wy] of [[745, 320], [800, 320], [855, 320], [745, 385], [855, 385]]) {
      keepArt.fillStyle(0x223047, 1);
      keepArt.fillRect(wx - 9, wy - 14, 18, 28);
      keepArt.fillStyle(0x94c8f8, 0.4);
      keepArt.fillRect(wx - 6, wy - 11, 12, 10);
    }
    // Keep door.
    keepArt.fillStyle(0x3a2a1c, 1);
    keepArt.fillRect(KEEP_X - 22, 406, 44, 37);
    keepArt.fillStyle(0x2c2118, 1);
    keepArt.fillRect(KEEP_X - 22, 406, 44, 5);
    this.placeLabel(KEEP_X, 462, "THE KEEP — CLAW AI", 12, "#9fd0ff");

    // Workshop where controlled tools run.
    graphics.fillStyle(PALETTE.timber, 1);
    graphics.fillRect(955, 420, 120, 90);
    graphics.fillStyle(0x7a5638, 1);
    for (let x = 960; x < 1070; x += 18) graphics.fillRect(x, 425, 4, 80);
    graphics.fillStyle(PALETTE.roofDark, 1);
    graphics.fillTriangle(945, 424, 1015, 366, 1085, 424);
    graphics.fillStyle(PALETTE.roof, 1);
    graphics.fillTriangle(953, 420, 1015, 372, 1077, 420);
    graphics.fillStyle(0x2c2118, 1);
    graphics.fillRect(1000, 470, 28, 40);
    this.placeLabel(WORKSHOP.x + 10, 526, "WORKSHOP — TOOLS", 11, "#f0cf9b");

    // Quarantine cell outside the walls.
    const cell = this.add.graphics();
    cell.fillStyle(0x39404a, 1);
    cell.fillRect(1090, 650, 180, 125);
    cell.lineStyle(4, PALETTE.locked, 1);
    cell.strokeRect(1090, 650, 180, 125);
    cell.lineStyle(3, 0x772f2f, 1);
    for (let x = 1108; x < 1270; x += 22) cell.lineBetween(x, 650, x, 775);
    this.placeLabel(QUARANTINE.x, 792, "QUARANTINE", 12, "#ffb0b0");

    // Gatehouse and portcullis: the HiddenLayer checkpoint.
    this.add.tileSprite(GATE_X, 560, 130, 90, "brick").setTileScale(2);
    graphics.fillStyle(PALETTE.stoneLight, 1);
    for (let x = GATE_X - 62; x < GATE_X + 55; x += 26) graphics.fillRect(x, 508, 16, 10);
    this.portcullis = this.add
      .tileSprite(GATE_X, GATE_Y, 72, 92, "portcullis")
      .setDepth(GATE_Y + 30);
    this.placeLabel(GATE_X, 648, "GATE — SCAN CHECKPOINT", 11, "#cfe3f5");

    // Guards permanently posted at the gate.
    for (const [x, flip] of [[512, false], [640, true]] as const) {
      const guard = this.add.sprite(x, 588, "guard_0").setScale(3).setDepth(588);
      guard.setFlipX(flip);
      guard.setInteractive({ cursor: "pointer" });
      this.wireHover(guard, () => ({
        entityId: null,
        kind: "guard",
        simulated: false,
        title: "Gate guard",
      }));
    }

    // Watchtower beacon reflecting the current trust state.
    this.beacon = this.add.circle(1120, 118, 11, PALETTE.normal).setDepth(1000);
    this.stateLabel = this.add
      .text(1120, 96, "NORMAL", {
        fontFamily: "monospace",
        fontSize: "13px",
        fontStyle: "bold",
        color: "#8ff0b4",
        stroke: "#0b1220",
        strokeThickness: 4,
      })
      .setOrigin(0.5, 1)
      .setResolution(3)
      .setDepth(1000);
  }

  private placeLabel(
    x: number,
    y: number,
    text: string,
    size: number,
    color: string,
  ): Phaser.GameObjects.Text {
    return this.add
      .text(x, y, text, {
        fontFamily: "monospace",
        fontSize: `${size}px`,
        fontStyle: "bold",
        color,
        stroke: "#0b1220",
        strokeThickness: 4,
      })
      .setOrigin(0.5)
      .setResolution(3)
      .setDepth(900);
  }

  // ------------------------------------------------------------- animations

  private spawnTraveler(entityId: string, simulated: boolean): void {
    const record = this.ensureEntity(entityId, "traveler", simulated);
    record.container.setPosition(90, 720);
    this.moveTo(record, 520, 600, 2200);
  }

  private inspect(entityId: string): void {
    const record = this.entities.get(entityId);
    if (!record) return;
    this.tweens.add({
      targets: record.sprite,
      angle: 5,
      yoyo: true,
      repeat: 3,
      duration: 110,
    });
  }

  private admitCitizen(entityId: string): void {
    const record = this.ensureEntity(entityId, "citizen", false);
    this.changeKind(record, "citizen");
    this.openGateBriefly();
    this.moveTo(record, 730 + this.entityOffset(entityId), 470, 1700);
  }

  private detain(entityId: string): void {
    const record = this.ensureEntity(entityId, "restricted", false);
    this.changeKind(record, "restricted");
    const slot = this.entityOffset(entityId) % 3;
    record.container.setPosition(462 - slot * 88, 622 + slot * 8);
  }

  private makeEnemy(entityId: string): void {
    const record = this.ensureEntity(entityId, "enemy", false);
    this.changeKind(record, "enemy");
    const slot = this.entityOffset(entityId) % 3;
    record.container.setPosition(392 - slot * 95, 618 + slot * 8);
  }

  private quarantine(entityId: string): void {
    const record = this.entities.get(entityId);
    if (!record) return;
    this.moveTo(record, QUARANTINE.x, QUARANTINE.y, 1900);
  }

  private launchWorker(entityId: string): void {
    const workerId = `${entityId}:worker`;
    const record = this.ensureEntity(workerId, "worker", false);
    record.label.setText("running tool");
    record.container.setPosition(KEEP_X, 430);
    this.moveTo(record, WORKSHOP.x, WORKSHOP.y, 1000);
  }

  private returnWorker(entityId: string): void {
    const record = this.entities.get(`${entityId}:worker`);
    if (!record) return;
    this.moveTo(record, KEEP_X, 430, 1000, () => {
      record.container.destroy();
      this.entities.delete(record.state.id);
    });
  }

  private launchMessenger(entityId: string): void {
    const messengerId = `${entityId}:messenger`;
    const record = this.ensureEntity(messengerId, "messenger", false);
    record.label.setText("delivering report");
    record.container.setPosition(KEEP_X, 430);
    const scroll = this.add.image(KEEP_X + 20, 412, "scroll").setScale(1.4).setDepth(2000);
    this.tweens.add({
      targets: scroll,
      x: "+=720",
      y: "+=370",
      duration: 2600,
      onComplete: () => scroll.destroy(),
    });
    this.moveTo(record, KEEP_X + 720, 800, 2600, () => {
      record.container.destroy();
      this.entities.delete(messengerId);
    });
  }

  private activateKeep(): void {
    this.tweens.add({
      targets: this.keepGlow,
      alpha: 0.75,
      yoyo: true,
      repeat: 2,
      duration: 280,
    });
  }

  private fireCrossbows(entityId: string): void {
    const record = this.entities.get(entityId);
    if (!record) return;
    const { x: targetX, y: targetY } = record.container;
    for (const [x, y] of [[480, 150], [1120, 150]]) {
      const arrow = this.add.image(x, y, "arrow").setDepth(2000);
      arrow.rotation = Phaser.Math.Angle.Between(x, y, targetX, targetY);
      this.tweens.add({
        targets: arrow,
        x: targetX,
        y: targetY,
        duration: 450,
        onComplete: () => {
          arrow.destroy();
          const impact = this.add.circle(targetX, targetY - 20, 6, 0xfff1c4, 0.9).setDepth(2001);
          this.tweens.add({
            targets: impact,
            scale: 4,
            alpha: 0,
            duration: 320,
            onComplete: () => impact.destroy(),
          });
        },
      });
    }
    this.tweens.add({
      targets: record.sprite,
      alpha: 0.2,
      yoyo: true,
      repeat: 4,
      duration: 120,
    });
  }

  private pulseBeacon(): void {
    this.tweens.add({
      targets: this.beacon,
      scale: 2.0,
      alpha: 0.25,
      yoyo: true,
      duration: 350,
    });
  }

  private openGateBriefly(): void {
    if (this.trustState === "LOCKED") return;
    this.tweens.add({
      targets: this.portcullis,
      y: GATE_Y - 66,
      duration: 350,
      yoyo: true,
      hold: 1100,
      ease: "Sine.easeInOut",
    });
  }

  private setTrustState(state: TrustState): void {
    if (!["NORMAL", "RESTRICTED", "LOCKED"].includes(state)) return;
    this.trustState = state;
    const color =
      state === "NORMAL"
        ? PALETTE.normal
        : state === "RESTRICTED"
          ? PALETTE.restricted
          : PALETTE.locked;
    this.beacon?.setFillStyle(color);
    this.stateLabel?.setText(state);
    this.stateLabel?.setColor(
      state === "NORMAL" ? "#8ff0b4" : state === "RESTRICTED" ? "#ffd28f" : "#ff9d9d",
    );
    if (this.portcullis) {
      this.portcullis.setTint(state === "LOCKED" ? 0xff9d9d : 0xffffff);
    }
    window.dispatchEvent(new CustomEvent("tower-trust-state", { detail: state }));
  }

  // --------------------------------------------------------------- entities

  private ensureEntity(
    entityId: string,
    kind: EntityState["kind"],
    simulated: boolean,
  ): EntityRecord {
    const existing = this.entities.get(entityId);
    if (existing) return existing;

    const shadow = this.add.ellipse(0, 28, 34, 10, 0x000000, 0.28);
    const sprite = this.add.sprite(0, 0, `${kind}_0`).setScale(3);
    const label = this.add
      .text(0, -42, defaultLabel(kind), {
        fontFamily: "monospace",
        fontSize: "11px",
        fontStyle: "bold",
        color: hexColor(KIND_COLORS[kind] ?? 0xffffff),
        backgroundColor: "rgba(11, 18, 32, 0.82)",
        padding: { x: 5, y: 2 },
      })
      .setOrigin(0.5, 1)
      .setResolution(3);
    const container = this.add.container(90, 720, [shadow, sprite, label]);
    container.setDepth(720);
    if (simulated) sprite.setTint(0xbfe3ff);

    sprite.setInteractive({ cursor: "pointer" });
    sprite.on("pointerdown", () => {
      window.dispatchEvent(new CustomEvent("tower-entity-selected", { detail: entityId }));
    });

    const record: EntityRecord = {
      state: { id: entityId, kind, eventIds: [], simulated },
      container,
      sprite,
      label,
    };
    this.wireHover(sprite, () => ({
      entityId,
      kind: record.state.kind,
      simulated: record.state.simulated,
      title: record.state.title ?? null,
    }));
    this.entities.set(entityId, record);
    return record;
  }

  private wireHover(
    sprite: Phaser.GameObjects.Sprite,
    detail: () => Omit<EntityHoverDetail, "x" | "y">,
  ): void {
    const dispatch = (pointer: Phaser.Input.Pointer): void => {
      window.dispatchEvent(
        new CustomEvent<EntityHoverDetail>("tower-entity-hover", {
          detail: { ...detail(), x: pointer.x, y: pointer.y },
        }),
      );
    };
    sprite.on("pointerover", dispatch);
    sprite.on("pointermove", dispatch);
    sprite.on("pointerout", () => {
      window.dispatchEvent(new CustomEvent("tower-entity-hover", { detail: null }));
    });
  }

  private changeKind(record: EntityRecord, kind: EntityState["kind"]): void {
    if (record.state.kind === kind) return;
    record.state.kind = kind;
    record.sprite.stop();
    record.sprite.setTexture(`${kind}_0`);
    record.label.setColor(hexColor(KIND_COLORS[kind] ?? 0xffffff));
    if (!record.state.title) record.label.setText(defaultLabel(kind));
    this.tweens.add({
      targets: record.sprite,
      scale: 3.6,
      yoyo: true,
      duration: 140,
    });
  }

  /** Tween movement with walk animation, facing, and depth sorting. */
  private moveTo(
    record: EntityRecord,
    x: number,
    y: number,
    duration: number,
    onComplete?: () => void,
  ): void {
    record.sprite.setFlipX(x < record.container.x);
    record.sprite.play(`walk_${record.state.kind}`, true);
    this.tweens.add({
      targets: record.container,
      x,
      y,
      duration,
      ease: "Sine.easeInOut",
      onUpdate: () => record.container.setDepth(record.container.y),
      onComplete: () => {
        if (!record.container.active) return;
        record.sprite.stop();
        record.sprite.setTexture(`${record.state.kind}_0`);
        onComplete?.();
      },
    });
  }

  private recordEvent(entityId: string, event: TowerEvent): void {
    const record = this.ensureEntity(
      entityId,
      event.type === "incident_created" ? "enemy" : "traveler",
      Boolean(event.payload.simulated),
    );
    record.state.eventIds.push(event.id);
    record.state.eventIds = record.state.eventIds.slice(-100);
  }

  private fitCamera(): void {
    const width = this.scale.width;
    const height = this.scale.height;
    const zoom = Math.max(width / WORLD_WIDTH, height / WORLD_HEIGHT);
    this.cameras.main.setZoom(zoom);
    this.cameras.main.centerOn(WORLD_WIDTH / 2, WORLD_HEIGHT / 2);
  }

  private entityOffset(entityId: string): number {
    return (
      [...entityId].reduce((total, character) => total + character.charCodeAt(0), 0) %
      120
    );
  }
}

function truncate(text: string, length: number): string {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function hexColor(color: number): string {
  return `#${color.toString(16).padStart(6, "0")}`;
}

function defaultLabel(kind: EntityState["kind"]): string {
  switch (kind) {
    case "traveler":
      return "incoming content";
    case "citizen":
      return "clean content";
    case "restricted":
      return "needs decision";
    case "enemy":
      return "blocked threat";
    case "worker":
      return "running tool";
    case "messenger":
      return "delivering report";
    default: {
      const exhaustive: never = kind;
      return exhaustive;
    }
  }
}
