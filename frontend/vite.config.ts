import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "../app/static/game",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      input: "src/main.ts",
      output: {
        entryFileNames: "game.js",
        assetFileNames: "game.[ext]",
      },
    },
  },
});
