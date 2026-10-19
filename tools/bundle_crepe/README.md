# bundle_crepe — 메모 패널 위지윅 편집기(Milkdown Crepe) 묶기

1. `cd tools/bundle_crepe && npm install` (버전은 package.json에 고정; node_modules·lock은 커밋하지 않는다)
2. `node build.mjs` → `src/md4paper/ui/assets/crepe/`의 crepe.min.js·crepe.min.css·VERSION·LICENSE를 다시 만든다
3. 버전을 올릴 때는 package.json의 `@milkdown/crepe`를 바꾸고 1–2를 돌린 뒤 결과물을 커밋한다
