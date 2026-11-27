// fetch-font.mjs — download the font that lets matplotlib draw Chinese text
// on the server (run.cjs): Noto Sans SC Regular from Noto CJK Sans 2.004 (the
// simplified Chinese subset, with Latin letters), under the SIL Open Font
// License 1.1, with that license. Pyodide's matplotlib has DejaVu Sans only,
// which has no Chinese characters: they were drawn as boxes.
//
// Run at image build time (python_runner/Dockerfile): node fetch-font.mjs <out dir>.
// Every file is checked against the hash below.
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const BASE = 'https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/';
const FILES = {
  'Sans/SubsetOTF/SC/NotoSansSC-Regular.otf': ['NotoSansSC-Regular.otf',
    'faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9'],
  LICENSE: ['LICENSE-NotoSansCJK.txt', '6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2'],
};

if (!process.argv[2]) throw new Error('usage: node fetch-font.mjs <out dir>');
await mkdir(process.argv[2], { recursive: true });

for (const [source, [name, sha256]] of Object.entries(FILES)) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      const response = await fetch(BASE + source);
      if (!response.ok) throw new Error(`${source}: HTTP ${response.status}`);
      const body = Buffer.from(await response.arrayBuffer());
      const got = createHash('sha256').update(body).digest('hex');
      if (got !== sha256) throw new Error(`${source}: sha256 ${got}, expected ${sha256}`);
      await writeFile(join(process.argv[2], name), body);
      break;
    } catch (error) {
      if (attempt >= 3) throw error;
      console.warn(`retrying ${source}: ${error.message}`);
    }
  }
}
