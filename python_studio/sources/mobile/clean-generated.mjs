import { readFile, writeFile } from 'node:fs/promises';

for (const file of ['android/app/capacitor.build.gradle', 'android/capacitor.settings.gradle']) {
  const content = await readFile(file, 'utf8');
  await writeFile(file, content.replace(/^\s*\/\/[^\n]*\n/gm, ''));
}
