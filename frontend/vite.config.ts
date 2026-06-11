import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// Build straight into frontend/dist with stable file names. The backend
// serves /static with Cache-Control: no-cache, so fixed names need no
// content hashing or manifest plumbing; islands are code-split chunks
// loaded on demand by src/main.ts.
export default defineConfig({
  plugins: [preact()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: "src/main.ts",
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "islands/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  test: {
    environment: "jsdom",
  },
});
