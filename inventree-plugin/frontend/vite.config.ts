import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteExternalsPlugin } from "vite-plugin-externals";

// InvenTree provides these at runtime on the window, so a plugin must not
// bundle its own copies -- two Reacts in one page break hooks and context.
// Names and mapping follow InvenTree's own plugin-creator template.
const externalLibs: Record<string, string> = {
  react: "React",
  "react-dom": "ReactDOM",
  "@mantine/core": "MantineCore",
  "@mantine/notifications": "MantineNotifications",
};

export default defineConfig({
  plugins: [react({ jsxRuntime: "classic" }), viteExternalsPlugin(externalLibs)],
  esbuild: { jsx: "preserve" },
  build: {
    target: "esnext",
    cssCodeSplit: false,
    sourcemap: true,
    rollupOptions: {
      preserveEntrySignatures: "exports-only",
      input: ["./src/Panel.tsx"],
      output: {
        dir: "../inventree_kicad_assembly_panel/static",
        entryFileNames: "[name].js",
        assetFileNames: "assets/[name].[ext]",
        globals: externalLibs,
      },
      external: Object.keys(externalLibs),
    },
  },
  optimizeDeps: { exclude: Object.keys(externalLibs) },
});
