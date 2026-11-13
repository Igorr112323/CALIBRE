import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const assetsDir = path.join(root, 'www', 'assets');
const files = await readdir(assetsDir);
const findAsset = (prefix, suffix) => {
  const matches = files.filter(name => name.startsWith(prefix) && name.endsWith(suffix));
  if (matches.length !== 1) throw new Error(`Expected one asset for ${prefix}${suffix}, found ${matches.length}`);
  return matches[0];
};
const dataUrl = async (name, type) => `data:${type};base64,${(await readFile(path.join(assetsDir, name))).toString('base64')}`;
let html = await readFile(path.join(root, 'www', 'index.html'), 'utf8');
const css = await readFile(path.join(assetsDir, findAsset('index-', '.css')), 'utf8');
let js = await readFile(path.join(assetsDir, findAsset('index-', '.js')), 'utf8');
const photo = findAsset('kolibri-grn471-', '.jpg');
const photoReference = 'new URL(`' + photo + '`,import.meta.url).href';
if (!js.includes(photoReference)) throw new Error('Photo asset reference not found in the built application');
js = js.replace(photoReference, JSON.stringify(await dataUrl(photo, 'image/jpeg')));
if (js.includes('import.meta') || js.toLowerCase().includes('</script')) throw new Error('The built script cannot be embedded safely');
const scriptTag = /<script type="module" crossorigin src="[^"]+"><\/script>/;
const styleTag = /<link rel="stylesheet" crossorigin href="[^"]+">/;
if (!scriptTag.test(html) || !styleTag.test(html)) throw new Error('Built HTML has an unexpected format');
html = html.replace(scriptTag, '').replace(styleTag, () => `<style>${css}</style>`);
for (const [prefix, suffix, type] of [['kolibri-intro-poster-', '.png', 'image/png'], ['kolibri-intro-', '.mp4', 'video/mp4'], ['kolibri-mark-', '.png', 'image/png']]) {
  const name = findAsset(prefix, suffix);
  const reference = `./assets/${name}`;
  if (!html.includes(reference)) throw new Error(`Asset reference not found: ${name}`);
  html = html.replaceAll(reference, await dataUrl(name, type));
}
if (html.includes('./assets/')) throw new Error('Unexpected external asset in the built page');
html = html.replace('</body>', `<script>${js}</script>\n</body>`);
const output = path.resolve(root, '../dist/Kolibri-stend.html');
await mkdir(path.dirname(output), { recursive: true });
await writeFile(output, html);
console.log(`Created ${output}`);
