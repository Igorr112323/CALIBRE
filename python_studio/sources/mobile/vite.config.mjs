import { defineConfig } from 'vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, '../web/app');

export default defineConfig({
  root: appRoot,

  base: './',
  publicDir: false,
  server: { host: '0.0.0.0', allowedHosts: true },
  preview: { host: '0.0.0.0', allowedHosts: true },
  resolve: {
    alias: {
      '@capacitor/core': path.resolve(here, 'node_modules/@capacitor/core/dist/index.js'),
    },
  },
  build: {
    assetsInlineLimit: 0,
    outDir: path.resolve(here, 'www'),
    emptyOutDir: true,
    sourcemap: false,
  },
});
