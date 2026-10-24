import { defineConfig } from 'vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, '../web/app');

export default defineConfig({
  root: appRoot,
  publicDir: false,
  resolve: {
    alias: {
      '@capacitor/core': path.resolve(here, 'node_modules/@capacitor/core/dist/index.js'),
      '@capacitor-community/sqlite': path.resolve(here, 'node_modules/@capacitor-community/sqlite/dist/esm/index.js'),
      '@capacitor/network': path.resolve(here, 'node_modules/@capacitor/network/dist/esm/index.js'),
    },
  },
  server: {
    // Intentional: desktop preview must never be reachable from a phone or another LAN device.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: path.resolve(here, 'www'),
    emptyOutDir: true,
    sourcemap: false,
  },
});
