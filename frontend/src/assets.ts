import Phaser from "phaser";

export const PALETTE = {
  grass: 0x2e6b3f,
  grassDark: 0x275c36,
  grassLight: 0x3a7d4a,
  road: 0xb08d5e,
  roadDark: 0x8f6f47,
  roadEdge: 0x6d513c,
  stone: 0x8b91a1,
  stoneLight: 0xb8bdc7,
  stoneDark: 0x51596a,
  mortar: 0x3d4452,
  timber: 0x60402f,
  roof: 0xa8433a,
  roofDark: 0x7e2f29,
  gold: 0xf2c15c,
  normal: 0x67d391,
  restricted: 0xf0ad4e,
  locked: 0xe65b5b,
  water: 0x254a69,
  ink: 0x111827,
};

/** Color of each character kind, reused by name tags, legend, and tooltips. */
export const KIND_COLORS: Record<string, number> = {
  traveler: 0x5da9e9,
  citizen: 0x67d391,
  restricted: 0xc07fd6,
  enemy: 0xe65b5b,
  guard: 0xb8bdc7,
  worker: 0xe0a458,
  messenger: 0x8ecdf5,
};

type PixelMap = string[];
type ColorKey = Record<string, number>;

interface CharacterArt {
  colors: ColorKey;
  idle: PixelMap;
  walk: PixelMap;
}

const SKIN = 0xf0c8a0;
const EYE = 0x141a24;
const BOOT = 0x4a3826;

/**
 * Two-frame pixel characters, 16 wide x 20 tall. '.' is transparent, every
 * other letter looks up a color in `colors`. Idle and walk differ in the legs.
 */
const CHARACTERS: Record<string, CharacterArt> = {
  traveler: {
    // Blue-cloaked wanderer with a brown cap and a satchel: incoming content.
    colors: {
      h: 0x6b4a2f, f: SKIN, e: EYE, c: 0x2d4a66, t: 0x4d83b8,
      d: 0x3b6791, g: 0x8a6237, l: 0x3a4a63, b: BOOT,
    },
    idle: [
      "................",
      ".....hhhhhh.....",
      "....hhhhhhhh....",
      "....ffffffff....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff.....",
      ".....cccccc.....",
      "....tttttttt....",
      "...fttttttttf...",
      "..ggttddddttf...",
      "..ggttddddttf...",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      "....bb....bb....",
      "....bb....bb....",
      "................",
    ],
    walk: [
      "................",
      ".....hhhhhh.....",
      "....hhhhhhhh....",
      "....ffffffff....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff.....",
      ".....cccccc.....",
      "....tttttttt....",
      "...fttttttttf...",
      "..ggttddddttf...",
      "..ggttddddttf...",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      "....ll....ll....",
      "...ll......ll...",
      "...bb......bb...",
      "..bb........bb..",
      "................",
    ],
  },
  citizen: {
    // Green-clad villager with straw hair: clean content living in the castle.
    colors: {
      h: 0xd8b45a, f: SKIN, e: EYE, c: 0x4c7d5c, t: 0x66a57a,
      d: 0xe2c16f, l: 0x54683f, b: BOOT,
    },
    idle: [
      "................",
      ".....hhhhhh.....",
      "....hhhhhhhh....",
      "....hffffffh....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff.....",
      ".....cccccc.....",
      "....tttttttt....",
      "...fttddddttf...",
      "...fttddddttf...",
      "...fttddddttf...",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      "....bb....bb....",
      "....bb....bb....",
      "................",
    ],
    walk: [
      "................",
      ".....hhhhhh.....",
      "....hhhhhhhh....",
      "....hffffffh....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff.....",
      ".....cccccc.....",
      "....tttttttt....",
      "...fttddddttf...",
      "...fttddddttf...",
      "...fttddddttf...",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      "....ll....ll....",
      "...ll......ll...",
      "...bb......bb...",
      "..bb........bb..",
      "................",
    ],
  },
  restricted: {
    // Hooded suspect, face in shadow with glowing eyes: awaiting a decision.
    colors: {
      h: 0x7b5b82, d: 0x5a3f63, s: 0x241a2b, e: 0xe98df0, t: 0x6b4d73,
      l: 0x40304a, b: 0x2c2135,
    },
    idle: [
      "................",
      ".....hhhhhh.....",
      "....hhhhhhhh....",
      "...hhssssssdd...",
      "...hhsessesdd...",
      "...hhssssssdd...",
      "....hssssssd....",
      ".....tttttt.....",
      "....tttttttt....",
      "...tttttttttt...",
      "...ttdddddttt...",
      "...ttdddddttt...",
      "...tttttttttt...",
      "....tttttttt....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      "....bb....bb....",
      "....bb....bb....",
      "................",
    ],
    walk: [
      "................",
      ".....hhhhhh.....",
      "....hhhhhhhh....",
      "...hhssssssdd...",
      "...hhsessesdd...",
      "...hhssssssdd...",
      "....hssssssd....",
      ".....tttttt.....",
      "....tttttttt....",
      "...tttttttttt...",
      "...ttdddddttt...",
      "...ttdddddttt...",
      "...tttttttttt...",
      "....tttttttt....",
      ".....ll..ll.....",
      "....ll....ll....",
      "...ll......ll...",
      "...bb......bb...",
      "..bb........bb..",
      "................",
    ],
  },
  enemy: {
    // Horned red raider with a sword: hostile content that got blocked.
    colors: {
      x: 0xd9cdb4, h: 0x3a1d20, s: 0x612730, e: 0xff6b5e, t: 0xa93d48,
      d: 0x7e2b34, w: 0xc7cbd3, g: 0x5b4a33, l: 0x4d2027, b: 0x2e1215,
    },
    idle: [
      "..x..........x..",
      "..xx.hhhhhh.xx..",
      "...xhhhhhhhhx...",
      "....ssssssss....",
      "....sesssses....",
      "....ssssssss....",
      ".....ssssss...w.",
      ".....tttttt...w.",
      "....tttttttt..w.",
      "...ttttttttt..w.",
      "...ttddddttt..g.",
      "...ttddddtttggg.",
      "...tttttttttt.g.",
      "....tttttttt....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      "....bb....bb....",
      "....bb....bb....",
      "................",
    ],
    walk: [
      "..x..........x..",
      "..xx.hhhhhh.xx..",
      "...xhhhhhhhhx...",
      "....ssssssss....",
      "....sesssses....",
      "....ssssssss....",
      ".....ssssss...w.",
      ".....tttttt...w.",
      "....tttttttt..w.",
      "...ttttttttt..w.",
      "...ttddddttt..g.",
      "...ttddddtttggg.",
      "...tttttttttt.g.",
      "....tttttttt....",
      ".....ll..ll.....",
      "....ll....ll....",
      "...ll......ll...",
      "...bb......bb...",
      "..bb........bb..",
      "................",
    ],
  },
  guard: {
    // Armored halberdier with a red plume: the HiddenLayer checkpoint.
    colors: {
      p: 0xcf4b41, h: 0x9aa2b2, f: SKIN, e: EYE, m: 0x7d8aa5,
      d: 0x5b6579, q: 0x6b4a2f, w: 0xd7dbe2, l: 0x4b5568, b: 0x363d4b,
    },
    idle: [
      "......pp......q.",
      ".....pppp....www",
      ".....hhhhhh...w.",
      "....hhhhhhhh..q.",
      "....hffffffh..q.",
      "....heffffeh..q.",
      "....hffffffh..q.",
      ".....mmmmmm...q.",
      "....mmmmmmmm..q.",
      "...fmmmmmmmm..q.",
      "...fmmddddmm..q.",
      "...fmmddddmmqqq.",
      "....mmmmmmmm..q.",
      ".....mmmmmm...q.",
      ".....ll..ll...q.",
      ".....ll..ll.....",
      ".....ll..ll.....",
      "....bb....bb....",
      "....bb....bb....",
      "................",
    ],
    walk: [
      "......pp......q.",
      ".....pppp....www",
      ".....hhhhhh...w.",
      "....hhhhhhhh..q.",
      "....hffffffh..q.",
      "....heffffeh..q.",
      "....hffffffh..q.",
      ".....mmmmmm...q.",
      "....mmmmmmmm..q.",
      "...fmmmmmmmm..q.",
      "...fmmddddmm..q.",
      "...fmmddddmmqqq.",
      "....mmmmmmmm..q.",
      ".....mmmmmm...q.",
      ".....ll..ll...q.",
      "....ll....ll....",
      "...ll......ll...",
      "...bb......bb...",
      "..bb........bb..",
      "................",
    ],
  },
  worker: {
    // Aproned smith with a hammer: a controlled tool being executed.
    colors: {
      r: 0xcf4b41, h: 0x4a3423, f: SKIN, e: EYE, t: 0xb07a3f,
      d: 0x6e4f30, g: 0x8b8f99, k: 0x5c4326, l: 0x5a4630, b: BOOT,
    },
    idle: [
      "................",
      ".....rrrrrr.....",
      "....hhhhhhhh....",
      "....ffffffff....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff...gg",
      ".....tttttt...gg",
      "....tttttttt..k.",
      "...ftddddddtf.k.",
      "...ftddddddtfkk.",
      "...ftddddddtf.k.",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      "....bb....bb....",
      "....bb....bb....",
      "................",
    ],
    walk: [
      "................",
      ".....rrrrrr.....",
      "....hhhhhhhh....",
      "....ffffffff....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff...gg",
      ".....tttttt...gg",
      "....tttttttt..k.",
      "...ftddddddtf.k.",
      "...ftddddddtfkk.",
      "...ftddddddtf.k.",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      "....ll....ll....",
      "...ll......ll...",
      "...bb......bb...",
      "..bb........bb..",
      "................",
    ],
  },
  messenger: {
    // Winged-cap courier with a scroll: the model's report leaving the keep.
    colors: {
      w: 0xf2f5f9, h: 0x386aa0, f: SKIN, e: EYE, t: 0x8ecdf5,
      d: 0x5d9ecf, g: 0xf5e7be, k: 0x9b723f, l: 0x46658a, b: BOOT,
    },
    idle: [
      "................",
      "..w..hhhhhh..w..",
      ".wwwhhhhhhhhwww.",
      "....ffffffff....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff.....",
      ".....tttttt.....",
      "....tttttttt....",
      "...fttddddttf...",
      "..kgttddddttf...",
      "..kgttddddttf...",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      ".....ll..ll.....",
      "....bb....bb....",
      "....bb....bb....",
      "................",
    ],
    walk: [
      "................",
      "..w..hhhhhh..w..",
      ".wwwhhhhhhhhwww.",
      "....ffffffff....",
      "....feffffef....",
      "....ffffffff....",
      ".....ffffff.....",
      ".....tttttt.....",
      "....tttttttt....",
      "...fttddddttf...",
      "..kgttddddttf...",
      "..kgttddddttf...",
      "....tttttttt....",
      ".....tttttt.....",
      ".....ll..ll.....",
      "....ll....ll....",
      "...ll......ll...",
      "...bb......bb...",
      "..bb........bb..",
      "................",
    ],
  },
};

const TREE: PixelMap = [
  "......gggg......",
  "....gGgggggg....",
  "...ggggggGggg...",
  "..gGgggggggggg..",
  "..ggggGggggGgg..",
  "..gggggggggggg..",
  "...ggGggggggg...",
  "....gggggGgg....",
  ".....gggggg.....",
  "......tttt......",
  "......tttt......",
  "......tdtt......",
  ".....ttttdt.....",
];

const TREE_COLORS: ColorKey = { g: 0x2c5c38, G: 0x3f7d4c, t: 0x5c4326, d: 0x453218 };

const BANNER: PixelMap = [
  "gggggggg",
  "rrrrrrrr",
  "rrrrrrrr",
  "rrgrrgrr",
  "rrrrrrrr",
  "rrrrrrrr",
  ".rrrrrr.",
  "..rrrr..",
  "...rr...",
];

const BANNER_COLORS: ColorKey = { r: 0xa8433a, g: 0xf2c15c };

function drawPixelMap(
  graphics: Phaser.GameObjects.Graphics,
  map: PixelMap,
  colors: ColorKey,
): void {
  map.forEach((row, y) => {
    [...row].forEach((cell, x) => {
      if (cell === ".") return;
      const color = colors[cell];
      if (color === undefined) return;
      graphics.fillStyle(color, 1);
      graphics.fillRect(x, y, 1, 1);
    });
  });
}

function pixelTexture(
  scene: Phaser.Scene,
  key: string,
  map: PixelMap,
  colors: ColorKey,
): void {
  const graphics = scene.make.graphics({ x: 0, y: 0 }, false);
  drawPixelMap(graphics, map, colors);
  graphics.generateTexture(key, map[0].length, map.length);
  graphics.destroy();
}

/** Deterministic pseudo-random generator so decoration never shifts between loads. */
export function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function grassTexture(scene: Phaser.Scene, key: string, seed: number): void {
  const graphics = scene.make.graphics({ x: 0, y: 0 }, false);
  const random = seededRandom(seed);
  graphics.fillStyle(PALETTE.grass, 1);
  graphics.fillRect(0, 0, 16, 16);
  for (let index = 0; index < 12; index += 1) {
    const x = Math.floor(random() * 16);
    const y = Math.floor(random() * 16);
    graphics.fillStyle(random() > 0.5 ? PALETTE.grassDark : PALETTE.grassLight, 1);
    graphics.fillRect(x, y, 1, random() > 0.7 ? 2 : 1);
  }
  graphics.generateTexture(key, 16, 16);
  graphics.destroy();
}

function dirtTexture(scene: Phaser.Scene): void {
  const graphics = scene.make.graphics({ x: 0, y: 0 }, false);
  const random = seededRandom(77);
  graphics.fillStyle(PALETTE.road, 1);
  graphics.fillRect(0, 0, 16, 16);
  for (let index = 0; index < 10; index += 1) {
    const x = Math.floor(random() * 16);
    const y = Math.floor(random() * 16);
    graphics.fillStyle(random() > 0.6 ? PALETTE.roadDark : 0xc29d6c, 1);
    graphics.fillRect(x, y, random() > 0.8 ? 2 : 1, 1);
  }
  graphics.generateTexture("dirt", 16, 16);
  graphics.destroy();
}

function brickTexture(scene: Phaser.Scene): void {
  const graphics = scene.make.graphics({ x: 0, y: 0 }, false);
  const random = seededRandom(41);
  graphics.fillStyle(PALETTE.mortar, 1);
  graphics.fillRect(0, 0, 16, 16);
  for (let row = 0; row < 4; row += 1) {
    const offset = row % 2 ? 4 : 0;
    for (let column = -1; column < 3; column += 1) {
      const x = column * 8 + offset;
      const shade = random();
      const color =
        shade > 0.75 ? PALETTE.stoneLight : shade > 0.2 ? PALETTE.stone : 0x767d8f;
      graphics.fillStyle(color, 1);
      graphics.fillRect(x + 1, row * 4 + 1, 6, 3);
    }
  }
  graphics.generateTexture("brick", 16, 16);
  graphics.destroy();
}

function portcullisTexture(scene: Phaser.Scene): void {
  const graphics = scene.make.graphics({ x: 0, y: 0 }, false);
  graphics.fillStyle(0x2c2118, 1);
  for (let x = 1; x < 24; x += 6) graphics.fillRect(x, 0, 2, 32);
  for (let y = 1; y < 32; y += 7) graphics.fillRect(0, y, 24, 2);
  graphics.fillStyle(0x4a392a, 1);
  for (let x = 1; x < 24; x += 6) graphics.fillRect(x, 0, 1, 32);
  graphics.generateTexture("portcullis", 24, 32);
  graphics.destroy();
}

function arrowTexture(scene: Phaser.Scene): void {
  const graphics = scene.make.graphics({ x: 0, y: 0 }, false);
  graphics.fillStyle(0x8a6237, 1);
  graphics.fillRect(0, 4, 24, 2);
  graphics.fillStyle(0xd7dbe2, 1);
  graphics.fillTriangle(24, 1, 31, 5, 24, 9);
  graphics.fillStyle(0xe8e4d5, 1);
  graphics.fillRect(0, 2, 4, 2);
  graphics.fillRect(0, 6, 4, 2);
  graphics.generateTexture("arrow", 32, 10);
  graphics.destroy();
}

function scrollTexture(scene: Phaser.Scene): void {
  const graphics = scene.make.graphics({ x: 0, y: 0 }, false);
  graphics.fillStyle(0xf5e7be, 1);
  graphics.fillRect(3, 2, 18, 15);
  graphics.fillStyle(0xcbb98a, 1);
  graphics.fillRect(6, 5, 12, 1);
  graphics.fillRect(6, 8, 12, 1);
  graphics.fillRect(6, 11, 9, 1);
  graphics.fillStyle(0x9b723f, 1);
  graphics.fillRect(0, 0, 4, 19);
  graphics.fillRect(20, 0, 4, 19);
  graphics.generateTexture("scroll", 24, 19);
  graphics.destroy();
}

export const CHARACTER_KINDS = Object.keys(CHARACTERS);

export function createProceduralPixelAssets(scene: Phaser.Scene): void {
  for (const [kind, art] of Object.entries(CHARACTERS)) {
    pixelTexture(scene, `${kind}_0`, art.idle, art.colors);
    pixelTexture(scene, `${kind}_1`, art.walk, art.colors);
  }
  pixelTexture(scene, "tree", TREE, TREE_COLORS);
  pixelTexture(scene, "banner", BANNER, BANNER_COLORS);
  grassTexture(scene, "grass", 11);
  grassTexture(scene, "grass2", 29);
  dirtTexture(scene);
  brickTexture(scene);
  portcullisTexture(scene);
  arrowTexture(scene);
  scrollTexture(scene);

  for (const kind of CHARACTER_KINDS) {
    if (scene.anims.exists(`walk_${kind}`)) continue;
    scene.anims.create({
      key: `walk_${kind}`,
      frames: [{ key: `${kind}_0` }, { key: `${kind}_1` }],
      frameRate: 7,
      repeat: -1,
    });
  }
}
