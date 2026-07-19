import Phaser from "phaser";

export const PALETTE = {
  grass: 0x183d2b,
  grassLight: 0x24573d,
  road: 0xa7835c,
  roadEdge: 0x6d513c,
  stone: 0x8b91a1,
  stoneLight: 0xb8bdc7,
  stoneDark: 0x51596a,
  timber: 0x60402f,
  gold: 0xf2c15c,
  normal: 0x67d391,
  restricted: 0xf0ad4e,
  locked: 0xe65b5b,
  water: 0x254a69,
  ink: 0x111827,
};

export function createProceduralPixelAssets(scene: Phaser.Scene): void {
  const graphics = scene.add.graphics();
  const sprite = (
    key: string,
    body: number,
    accent: number,
    width = 24,
    height = 32,
  ): void => {
    graphics.clear();
    graphics.fillStyle(0x000000, 0.25);
    graphics.fillRect(3, height - 5, width - 4, 4);
    graphics.fillStyle(body, 1);
    graphics.fillRect(5, 10, width - 10, height - 14);
    graphics.fillStyle(accent, 1);
    graphics.fillRect(4, 5, width - 8, 9);
    graphics.fillStyle(0xf4d7b5, 1);
    graphics.fillRect(8, 8, width - 16, 7);
    graphics.fillStyle(PALETTE.ink, 1);
    graphics.fillRect(8, 15, 3, 3);
    graphics.fillRect(width - 11, 15, 3, 3);
    graphics.generateTexture(key, width, height);
  };
  sprite("traveler", 0x4d83b8, 0x274767);
  sprite("citizen", 0x66a57a, 0xe2c16f);
  sprite("restricted", 0x7b5b82, 0x2f2638);
  sprite("enemy", 0xa93d48, 0x35191d);
  sprite("guard", 0x596477, 0xc7cbd3);
  sprite("worker", 0x9b6b3d, 0xe1a857);
  sprite("messenger", 0x386aa0, 0xf0e7c2);

  graphics.clear();
  graphics.fillStyle(PALETTE.gold, 1);
  graphics.fillRect(0, 3, 26, 4);
  graphics.fillStyle(0xf8e6a0, 1);
  graphics.fillTriangle(26, 0, 38, 5, 26, 10);
  graphics.generateTexture("arrow", 38, 10);

  graphics.clear();
  graphics.fillStyle(0xf5e7be, 1);
  graphics.fillRect(3, 2, 18, 15);
  graphics.fillStyle(0x9b723f, 1);
  graphics.fillRect(0, 0, 4, 19);
  graphics.fillRect(20, 0, 4, 19);
  graphics.generateTexture("scroll", 24, 19);
  graphics.destroy();
}
