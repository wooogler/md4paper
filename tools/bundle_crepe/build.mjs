// Milkdown Crepe → src/narani/ui/assets/crepe/{crepe.min.js, crepe.min.css, VERSION}
// 앱은 오프라인·빌드 도구 없이 돌므로, 개발 시점에 한 번 묶어 결과물을 커밋한다.
import { build } from 'esbuild';
import { mkdirSync, readFileSync, writeFileSync, copyFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', '..', 'src', 'narani', 'ui', 'assets', 'crepe');
const pkg = join(here, 'node_modules', '@milkdown', 'crepe');
const theme = join(pkg, 'lib', 'theme');
mkdirSync(out, { recursive: true });

await build({
  entryPoints: [join(here, 'entry.js')],
  bundle: true, minify: true, format: 'iife', target: 'es2020',
  legalComments: 'none',
  outfile: join(out, 'crepe.min.js'),
});

// CSS: 공통 + frame(밝은) 한 벌, 어두운 변수는 prefers-color-scheme: dark 안에 싼다(앱 나머지와 같은 규칙).
const dark = readFileSync(join(theme, 'frame-dark', 'style.css'), 'utf8');
const css = [
  `@import "${join(theme, 'common', 'style.css')}";`,
  `@import "${join(theme, 'frame', 'style.css')}";`,
  `@media (prefers-color-scheme: dark) {\n${dark}\n}`,
].join('\n');
await build({
  stdin: { contents: css, loader: 'css', resolveDir: here },
  bundle: true, minify: true, legalComments: 'none',
  loader: { '.woff': 'dataurl', '.woff2': 'dataurl', '.ttf': 'dataurl', '.svg': 'dataurl' },
  write: false,
}).then(r => {
  // KaTeX 글꼴이 woff2·woff·ttf 세 벌로 들어온다 — 브라우저는 woff2만 쓰므로 나머지 둘은 뺀다.
  const text = r.outputFiles[0].text
    .replace(/,url\(data:font\/(woff|ttf);base64,[^)]*\) format\("(woff|truetype)"\)/g, '');
  writeFileSync(join(out, 'crepe.min.css'), text);
});

const ver = JSON.parse(readFileSync(join(pkg, 'package.json'), 'utf8')).version;
const esb = JSON.parse(readFileSync(join(here, 'node_modules', 'esbuild', 'package.json'), 'utf8')).version;
writeFileSync(join(out, 'VERSION'),
  `@milkdown/crepe ${ver} (MIT)\nbundled with esbuild ${esb} on ${new Date().toISOString().slice(0, 10)}\n` +
  `theme: common + frame (frame-dark under prefers-color-scheme: dark)\n` +
  `regenerate: see tools/bundle_crepe/README.md\n`);
copyFileSync(join(pkg, 'LICENSE'), join(out, 'LICENSE'));
console.log('ok', ver);
