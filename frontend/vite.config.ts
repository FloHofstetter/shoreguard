import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// Build straight into frontend/dist with stable file names. The backend
// serves /static with Cache-Control: no-cache, so fixed names need no
// content hashing or manifest plumbing; islands are code-split chunks
// loaded on demand by src/main.ts.
export default defineConfig({
  plugins: [preact()],
  // The bundle is served under /static/dist (dev and wheel alike). Vite
  // resolves modulepreload deps against this base; the default "/" made
  // the browser request /islands/… and 404 on every island load.
  base: "/static/dist/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: {
        main: "src/main.ts",
        "theme-init": "src/theme-init.ts",
      },
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
