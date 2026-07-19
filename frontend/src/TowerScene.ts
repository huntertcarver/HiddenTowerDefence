import Phaser from "phaser";

import { createProceduralPixelAssets, PALETTE } from "./assets";
import type { EntityState, SceneSnapshot, TowerEvent, TrustState } from "./types";

interface EntityRecord {
  state: EntityState;
  sprite: Phaser.GameObjects.Sprite;
}

export class TowerScene extends Phaser.Scene {
  private readonly entities = new Map<string, EntityRecord>();
  private readonly processedEventIds = new Set<number>();
  private gate!: Phaser.GameObjects.Rectangle;
  private beacon!: Phaser.GameObjects.Arc;
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
    this.cameras.main.setBounds(0, 0, 1600, 900);
    this.physics.world.setBounds(0, 0, 1600, 900);
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
        if (entityId) this.spawnTraveler(entityId, Boolean(event.payload.simulated));
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
    this.cameras.main.pan(record.sprite.x, record.sprite.y, 450, "Sine.easeInOut");
    this.tweens.add({
      targets: record.sprite,
      alpha: 0.35,
      yoyo: true,
      repeat: 3,
      duration: 120,
    });
  }

  private drawWorld(): void {
    const graphics = this.add.graphics();
    for (let y = 0; y < 900; y += 32) {
      for (let x = 0; x < 1600; x += 32) {
        graphics.fillStyle((x / 32 + y / 32) % 2 ? PALETTE.grass : PALETTE.grassLight);
        graphics.fillRect(x, y, 32, 32);
      }
    }
    graphics.fillStyle(PALETTE.roadEdge, 1);
    graphics.fillPoints(
      [
        new Phaser.Math.Vector2(0, 690),
        new Phaser.Math.Vector2(565, 535),
        new Phaser.Math.Vector2(1010, 535),
        new Phaser.Math.Vector2(1600, 770),
        new Phaser.Math.Vector2(1600, 880),
        new Phaser.Math.Vector2(1010, 620),
        new Phaser.Math.Vector2(565, 620),
        new Phaser.Math.Vector2(0, 785),
      ],
      true,
    );
    graphics.fillStyle(PALETTE.road, 1);
    graphics.fillPoints(
      [
        new Phaser.Math.Vector2(0, 710),
        new Phaser.Math.Vector2(575, 555),
        new Phaser.Math.Vector2(1000, 555),
        new Phaser.Math.Vector2(1600, 790),
        new Phaser.Math.Vector2(1600, 845),
        new Phaser.Math.Vector2(1000, 595),
        new Phaser.Math.Vector2(575, 595),
        new Phaser.Math.Vector2(0, 760),
      ],
      true,
    );

    this.drawCastle(graphics);
    this.add
      .text(800, 100, "HIDDEN TOWER", {
        fontFamily: "monospace",
        fontSize: "22px",
        color: "#f5e7be",
        stroke: "#111827",
        strokeThickness: 5,
      })
      .setOrigin(0.5)
      .setResolution(2);
  }

  private drawCastle(graphics: Phaser.GameObjects.Graphics): void {
    graphics.fillStyle(PALETTE.stoneDark, 1);
    graphics.fillRect(450, 170, 700, 410);
    graphics.fillStyle(PALETTE.stone, 1);
    graphics.fillRect(470, 190, 660, 365);
    graphics.fillStyle(PALETTE.grass, 1);
    graphics.fillRect(545, 265, 510, 290);
    for (const [x, y] of [
      [430, 140],
      [1070, 140],
      [430, 485],
      [1070, 485],
    ]) {
      graphics.fillStyle(PALETTE.stoneDark, 1);
      graphics.fillRect(x, y, 100, 110);
      graphics.fillStyle(PALETTE.stoneLight, 1);
      graphics.fillRect(x + 12, y + 12, 76, 80);
      for (let offset = 0; offset < 4; offset += 1) {
        graphics.fillRect(x + offset * 25, y - 10, 18, 20);
      }
    }
    graphics.fillStyle(PALETTE.stoneDark, 1);
    graphics.fillRect(665, 220, 270, 230);
    graphics.fillStyle(PALETTE.stoneLight, 1);
    graphics.fillRect(685, 240, 230, 190);
    this.keepGlow = this.add.rectangle(800, 330, 150, 115, 0x4da3ff, 0.08);

    graphics.fillStyle(PALETTE.timber, 1);
    graphics.fillRect(960, 390, 120, 115);
    graphics.fillStyle(0xb97942, 1);
    graphics.fillTriangle(950, 395, 1020, 340, 1090, 395);
    this.add.text(1020, 460, "WORKSHOP", {
      fontFamily: "monospace",
      fontSize: "12px",
      color: "#f5e7be",
    }).setOrigin(0.5);

    graphics.fillStyle(0x4f5963, 1);
    graphics.fillRect(1090, 650, 180, 125);
    graphics.lineStyle(5, PALETTE.locked, 1);
    graphics.strokeRect(1090, 650, 180, 125);
    this.add.text(1180, 715, "QUARANTINE", {
      fontFamily: "monospace",
      fontSize: "13px",
      color: "#ffb0b0",
    }).setOrigin(0.5);

    this.gate = this.add.rectangle(575, 575, 72, 104, PALETTE.timber);
    this.gate.setStrokeStyle(7, PALETTE.stoneDark);
    this.beacon = this.add.circle(1120, 132, 12, PALETTE.normal);
    this.add.sprite(530, 560, "guard").setScale(1.25);
    this.add.sprite(620, 560, "guard").setScale(1.25);
  }

  private spawnTraveler(entityId: string, simulated: boolean): void {
    const record = this.ensureEntity(entityId, "traveler", simulated);
    record.sprite.setPosition(90, 720);
    this.tweens.add({
      targets: record.sprite,
      x: 520,
      y: 590,
      duration: 2200,
      ease: "Linear",
    });
  }

  private inspect(entityId: string): void {
    const record = this.entities.get(entityId);
    if (!record) return;
    this.tweens.add({
      targets: record.sprite,
      angle: 4,
      yoyo: true,
      repeat: 3,
      duration: 110,
    });
  }

  private admitCitizen(entityId: string): void {
    const record = this.ensureEntity(entityId, "citizen", false);
    this.changeKind(record, "citizen");
    this.tweens.add({
      targets: this.gate,
      scaleY: 0.15,
      duration: 300,
      yoyo: true,
      hold: 900,
    });
    this.tweens.add({
      targets: record.sprite,
      x: 750 + this.entityOffset(entityId),
      y: 500,
      duration: 1600,
      ease: "Sine.easeInOut",
    });
  }

  private detain(entityId: string): void {
    const record = this.ensureEntity(entityId, "restricted", false);
    this.changeKind(record, "restricted");
    record.sprite.setPosition(535, 615);
  }

  private makeEnemy(entityId: string): void {
    const record = this.ensureEntity(entityId, "enemy", false);
    this.changeKind(record, "enemy");
    record.sprite.setPosition(500, 625);
  }

  private quarantine(entityId: string): void {
    const record = this.entities.get(entityId);
    if (!record) return;
    this.tweens.add({
      targets: record.sprite,
      x: 1180,
      y: 715,
      duration: 1900,
      ease: "Sine.easeInOut",
    });
  }

  private launchWorker(entityId: string): void {
    const workerId = `${entityId}:worker`;
    const record = this.ensureEntity(workerId, "worker", false);
    record.sprite.setPosition(800, 500);
    this.tweens.add({
      targets: record.sprite,
      x: 1020,
      y: 455,
      duration: 1000,
    });
  }

  private returnWorker(entityId: string): void {
    const record = this.entities.get(`${entityId}:worker`);
    if (!record) return;
    this.tweens.add({
      targets: record.sprite,
      x: 800,
      y: 500,
      duration: 1000,
    });
  }

  private launchMessenger(entityId: string): void {
    const messengerId = `${entityId}:messenger`;
    const record = this.ensureEntity(messengerId, "messenger", false);
    record.sprite.setPosition(800, 410);
    const scroll = this.add.image(814, 395, "scroll").setScale(0.65);
    this.tweens.add({
      targets: [record.sprite, scroll],
      x: "+=720",
      y: "+=370",
      duration: 2600,
      onComplete: () => {
        record.sprite.destroy();
        scroll.destroy();
        this.entities.delete(messengerId);
      },
    });
  }

  private activateKeep(): void {
    this.tweens.add({
      targets: this.keepGlow,
      alpha: 0.8,
      yoyo: true,
      repeat: 2,
      duration: 280,
    });
  }

  private fireCrossbows(entityId: string): void {
    const target = this.entities.get(entityId)?.sprite;
    if (!target) return;
    for (const [x, y] of [
      [480, 190],
      [1120, 190],
    ]) {
      const arrow = this.add.image(x, y, "arrow");
      arrow.rotation = Phaser.Math.Angle.Between(x, y, target.x, target.y);
      this.tweens.add({
        targets: arrow,
        x: target.x,
        y: target.y,
        duration: 500,
        onComplete: () => arrow.destroy(),
      });
    }
    this.tweens.add({
      targets: target,
      tint: 0xffffff,
      alpha: 0.2,
      yoyo: true,
      repeat: 4,
      duration: 120,
    });
  }

  private pulseBeacon(): void {
    this.tweens.add({
      targets: this.beacon,
      scale: 2.2,
      alpha: 0.2,
      yoyo: true,
      duration: 350,
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
    this.gate?.setFillStyle(state === "LOCKED" ? 0x341d1d : PALETTE.timber);
    window.dispatchEvent(new CustomEvent("tower-trust-state", { detail: state }));
  }

  private ensureEntity(
    entityId: string,
    kind: EntityState["kind"],
    simulated: boolean,
  ): EntityRecord {
    const existing = this.entities.get(entityId);
    if (existing) return existing;
    const sprite = this.add.sprite(90, 720, kind).setScale(1.5).setInteractive();
    sprite.setData("entityId", entityId);
    if (simulated) sprite.setTint(0x9dd9ff);
    sprite.on("pointerdown", () => {
      window.dispatchEvent(new CustomEvent("tower-entity-selected", { detail: entityId }));
    });
    const record: EntityRecord = {
      state: { id: entityId, kind, eventIds: [], simulated },
      sprite,
    };
    this.entities.set(entityId, record);
    return record;
  }

  private changeKind(record: EntityRecord, kind: EntityState["kind"]): void {
    record.state.kind = kind;
    record.sprite.setTexture(kind);
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
    const zoom = Math.max(width / 1600, height / 900);
    this.cameras.main.setZoom(zoom);
    this.cameras.main.centerOn(800, 450);
  }

  private entityOffset(entityId: string): number {
    return (
      [...entityId].reduce((total, character) => total + character.charCodeAt(0), 0) %
      120
    );
  }
}
