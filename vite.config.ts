import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "hushclaw/web/react-dist",
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
    rollupOptions: {
      input: "hushclaw/web/react-src/react-islands.tsx",
      output: {
        inlineDynamicImports: true,
        entryFileNames: "react-islands.js",
        assetFileNames: "react-islands[extname]",
      },
    },
  },
});
