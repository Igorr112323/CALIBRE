import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = path.join(here, 'www', 'index.html');
const runtimePath = path.join(here, 'www', 'runtime-catalog.js');
let html = await readFile(htmlPath, 'utf8');
const marker = '<script src="./runtime-catalog.js"><' + '/script>';
if (!html.includes(marker)) {
  const moduleTag = /<script type="module"[^>]*><\/script>/;
  if (!moduleTag.test(html)) throw new Error('Built HTML has no application script tag');
  html = html.replace(moduleTag, match => marker + match);
  await writeFile(htmlPath, html);
}
await writeFile(runtimePath, 'window.__KOLIBRI_CATALOG__={"machines":[]};\n');
