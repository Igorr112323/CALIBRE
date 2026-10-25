import { defineConfig } from 'vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, '../web/app');

export default defineConfig({
  root: appRoot,
  // The mobile interface is packaged in the APK; the PC serves only admin/API.
  base: './',
  publicDir: false,
  resolve: {
    alias: {
      '@capacitor/core': path.resolve(here, 'node_modules/@capacitor/core/dist/index.js'),
      '@capacitor-community/sqlite': path.resolve(here, 'node_modules/@capacitor-community/sqlite/dist/esm/index.js'),
      '@capacitor/network': path.resolve(here, 'node_modules/@capacitor/network/dist/esm/index.js'),
    },
  },
  build: {
    outDir: path.resolve(here, 'www'),
    emptyOutDir: true,
    sourcemap: false,
  },
});
