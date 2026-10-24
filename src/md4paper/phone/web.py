"""폰 리더 웹앱(PWA) — 정적 파일 몇 개. 어디에 올려 두든(GitHub Pages 등) 서버 코드 없이 돈다.

화면은 모바일 HTML(§mobile)과 같다 — 같은 CSS, 같은 읽기 동작(`mobile.CORE_JS`: 보기 모드·글자 크기·
문장 짝 시트·인용/각주 시트). 다른 점:
  - 논문을 **하나씩** 저장소에서 받아(§phone.bundle) 꽂는다. 목록은 index.json.
  - 받은 것은 Cache Storage에 둔다 — 비행기 안에서도 목록과 받아 둔 논문이 열린다. 같은 지문(hash)이면
    다시 받지 않는다. 앱 껍데기(이 파일들)는 서비스 워커가 캐시한다.
  - 화면 문구는 브라우저 언어(또는 데스크톱이 index.json에 적은 화면 언어)로 — 카탈로그를 다 싣는다.

저장소: `local`(같은 주소의 `data/` — `md4paper phone preview`가 로컬 폴더를 그 자리에 붙인다).
Google Drive 어댑터가 같은 `get(name)` 모양으로 들어온다.

JS는 이 저장소의 관례대로 파이썬 문자열에 둔다 — 그래야 md4T 문구를 i18n 카탈로그가 모은다.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from md4paper import i18n
from md4paper.ui import mobile, sentpair

APP_NAME = "Narani"

_PWA_CSS = """
.lib-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 4px 0 6px; color: var(--muted);
  font-size: .8em; line-height: 1.4; }
.lib-bar .grow { flex: 1; min-width: 12em; }
.lib-bar button { border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 9px;
  font: inherit; padding: 6px 10px; min-height: 32px; }
.lib-bar button:disabled { opacity: .55; }
.badge.off { border-style: dashed; }
.li-title .dl { color: var(--muted); font-weight: 400; font-size: .8em; margin-left: 4px; }
#loading { color: var(--muted); text-align: center; margin: 30vh 0 0; font-size: .95em; }
#lib .empty { color: var(--muted); font-size: .9em; padding: 12px 2px; }
"""

# 화면 언어 고르기 + md4T — 읽기 동작(CORE_JS)이 돌기 전에 있어야 한다
_PRELUDE_JS = r"""
(function(){
  var CFG = window.NARANI_CFG || {}, CATS = CFG.catalogs || {};
  function known(l){ return !!l && (l in CATS || l === CFG.source_lang); }
  function pick(){
    var saved = null;
    try { saved = localStorage.getItem('narani:ui_lang'); } catch (e) {}
    if (known(saved)) return saved;
    var prefs = navigator.languages || [navigator.language || ''], i, l;
    for (i = 0; i < prefs.length; i++){
      l = String(prefs[i]);
      if (known(l)) return l;
      if (/^zh-(TW|HK|MO|Hant)/i.test(l) && known('zh-Hant')) return 'zh-Hant';
      if (/^zh/i.test(l) && known('zh-Hans')) return 'zh-Hans';
      if (known(l.split('-')[0])) return l.split('-')[0];
    }
    return known(CFG.fallback_lang) ? CFG.fallback_lang : CFG.source_lang;
  }
  window.md4Lang = pick();
  window.md4T = function(s, kw){
    var r = (CATS[window.md4Lang] || {})[s] || s;
    if (kw) r = r.replace(/\{(\w+)\}/g, function(m, k){ return (k in kw) ? kw[k] : m; });
    return r;
  };
})();
"""

# 목록 ↔ 논문 — 논문은 저장소(또는 캐시)에서 받아 하나씩 꽂는다. mobile._ROUTE_JS의 자리.
_APP_JS = r"""
  var CFG = window.NARANI_CFG || {};
  var PID = 'p0', DC = 'narani-data';
  var main = doc.querySelector('main'), libList = doc.getElementById('lib-list'),
      libBar = doc.getElementById('lib-bar'), loading = doc.getElementById('loading');
  var DATA = {}, INDEX = null, loadSeq = 0, routed = false;
  var APP = CFG.app_name || 'Narani';

  // ---- 화면 문구 ----
  function setText(id, s){ var el = doc.getElementById(id); if (el) el.textContent = s; }
  function labels(){
    doc.documentElement.lang = md4Lang;
    setText('lbl-ko', md4T('번역')); setText('lbl-en', md4T('원문')); setText('lbl-both', md4T('대조'));
    setText('fs-dn', md4T('가') + '−'); setText('fs-up', md4T('가') + '+');
    doc.getElementById('fs-dn').setAttribute('aria-label', md4T('글자 작게'));
    doc.getElementById('fs-up').setAttribute('aria-label', md4T('글자 크게'));
    doc.getElementById('back').setAttribute('aria-label', md4T('논문 목록'));
  }
  function setUiLang(l){
    var cats = CFG.catalogs || {};
    if (!l || l === md4Lang || !(l in cats || l === CFG.source_lang)) return;
    try { localStorage.setItem('narani:ui_lang', l); } catch (e) {}
    window.md4Lang = l; labels();
  }

  // ---- 저장소 ----
  function localSource(){
    return {get: function(name){
      return fetch('data/' + name, {cache: 'no-store'}).then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }};
  }
  // Google Drive — 데스크톱이 만든 `Narani` 폴더(같은 Cloud 프로젝트라 drive.file로 보인다)를 읽는다.
  // 로그인은 팝업 대신 **리디렉트**(OAuth 클라이언트 측 흐름) — iOS 홈 화면 앱에선 팝업이 막히거나
  // 다른 창으로 열린다. 돌아온 주소의 #access_token을 읽고 원래 화면(#!…)으로 되돌린다.
  // access token은 한 시간짜리다. 끊기면 읽던 것(캐시)은 그대로 열리고, 새로 받을 때만 다시 연결한다.
  var GSCOPE = 'https://www.googleapis.com/auth/drive.file', GAPI = 'https://www.googleapis.com/drive/v3';
  var authError = '';
  function qs(o){ return Object.keys(o).map(function(k){ return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
  function takeRedirect(){
    var h = location.hash.replace(/^#/, '');
    if (!/(^|&)(access_token|error)=/.test(h)) return;
    var p = {}, saved = null;
    h.split('&').forEach(function(kv){ var i = kv.indexOf('='); if (i > 0) p[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); });
    try { saved = JSON.parse(sessionStorage.getItem('narani:gstate') || 'null'); sessionStorage.removeItem('narani:gstate'); } catch (e) {}
    history.replaceState(null, '', location.pathname + location.search + ((saved && saved.back) || ''));
    if (!saved || saved.s !== p.state) return;
    if (p.access_token){
      try {
        localStorage.setItem('narani:gtok', JSON.stringify({at: p.access_token, exp: Date.now() + (+p.expires_in || 3600) * 1000}));
        localStorage.setItem('narani:gconn', '1');
      } catch (e) {}
    } else authError = p.error || 'error';
  }
  function gToken(){
    try { var t = JSON.parse(localStorage.getItem('narani:gtok') || 'null'); return t && t.exp > Date.now() + 60000 ? t.at : null; }
    catch (e) { return null; }
  }
  function gSignIn(){
    var st = Math.random().toString(36).slice(2) + Date.now().toString(36);
    try { sessionStorage.setItem('narani:gstate', JSON.stringify({s: st, back: location.hash})); } catch (e) {}
    var o = {client_id: CFG.google_client_id, redirect_uri: location.origin + location.pathname, response_type: 'token',
             scope: GSCOPE, include_granted_scopes: 'true', state: st};
    location.href = 'https://accounts.google.com/o/oauth2/v2/auth?' + qs(o);
  }
  function driveSource(){
    var folder = null, ids = null;
    function fail(kind){ var e = new Error(kind); e.kind = kind; return e; }
    function api(url){
      var t = gToken();
      if (!t) return Promise.reject(fail('auth'));
      return fetch(url, {headers: {Authorization: 'Bearer ' + t}}).then(function(r){
        if (r.status === 401){ try { localStorage.removeItem('narani:gtok'); } catch (e) {} throw fail('auth'); }
        if (!r.ok) throw fail('http ' + r.status);
        return r;
      });
    }
    function list(q){
      var out = [];
      function page(tok){
        var u = GAPI + '/files?' + qs({q: q, fields: 'nextPageToken,files(id,name,modifiedTime)', pageSize: 1000,
                                       spaces: 'drive', orderBy: 'modifiedTime desc'}) + (tok ? '&pageToken=' + encodeURIComponent(tok) : '');
        return api(u).then(function(r){ return r.json(); }).then(function(d){
          out = out.concat(d.files || []);
          return d.nextPageToken ? page(d.nextPageToken) : out;
        });
      }
      return page(null);
    }
    function findFolder(){
      if (folder) return Promise.resolve(folder);
      return list("mimeType = 'application/vnd.google-apps.folder' and trashed = false and appProperties has { key='narani' and value='store' }")
        .then(function(fs){
          if (!fs.length) throw fail('nofolder');
          folder = fs[0].id; return folder;
        });
    }
    function loadIds(){
      return findFolder().then(function(f){ return list("'" + f + "' in parents and trashed = false"); }).then(function(fs){
        ids = {}; fs.forEach(function(x){ if (!(x.name in ids)) ids[x.name] = x.id; }); return ids;
      });
    }
    return {
      drive: true,
      get: function(name){
        // 목록을 받을 때마다 이름표를 새로 받는다 — 데스크톱이 그사이 새 번들을 올렸을 수 있다
        var p = (name === 'index.json' || !ids) ? loadIds() : Promise.resolve(ids);
        return p.then(function(m){
          if (!m[name]) throw fail(name === 'index.json' ? 'nofolder' : 'missing');
          return api(GAPI + '/files/' + m[name] + '?alt=media').then(function(r){ return r.json(); });
        });
      }
    };
  }
  takeRedirect();
  var source = CFG.local ? localSource() : (CFG.google_client_id ? driveSource() : null);
  function sourceProblem(err){
    // 저장소 오류 → 목록 위에 보일 안내와 단추
    var kind = err && err.kind;
    if (source && source.drive && (kind === 'auth' || authError)){
      var first = true;
      try { first = !localStorage.getItem('narani:gconn'); } catch (e) {}
      return {text: first ? md4T('Google Drive에 연결해 데스크톱에서 보낸 논문을 받아요.') : md4T('Google Drive 연결이 만료됐어요.'),
              button: first ? md4T('Google Drive 연결') : md4T('다시 연결'), act: function(){ gSignIn(); }};
    }
    if (kind === 'nofolder') return {text: md4T('Drive에 Narani 폴더가 없어요. 데스크톱 앱에서 먼저 폰으로 보내 주세요.')};
    return {text: md4T('목록을 불러오지 못했어요. 인터넷 연결을 확인하세요.')};
  }

  // ---- 오프라인 캐시 (받은 목록·번들) ----
  function cacheGet(name){
    if (!window.caches) return Promise.resolve(null);
    return caches.open(DC).then(function(c){ return c.match('__data/' + name); })
      .then(function(r){ return r ? r.json() : null; }).catch(function(){ return null; });
  }
  function cachePut(name, obj){
    if (!window.caches) return Promise.resolve();
    return caches.open(DC).then(function(c){
      return c.put('__data/' + name, new Response(JSON.stringify(obj), {headers: {'Content-Type': 'application/json'}}));
    }).catch(function(){});
  }
  function haveMap(){ try { return JSON.parse(localStorage.getItem('narani:have') || '{}') || {}; } catch (e) { return {}; } }
  function setHave(file, hash){
    var m = haveMap(); if (hash) m[file] = hash; else delete m[file];
    try { localStorage.setItem('narani:have', JSON.stringify(m)); } catch (e) {}
  }
  function pruneCache(ix){
    // 목록에서 빠진 논문의 캐시는 지운다 (폰 저장 공간)
    var keep = {}, m = haveMap(), k;
    ix.papers.forEach(function(e){ keep[e.file] = 1; });
    for (k in m) if (!keep[k]) setHave(k, null);
    if (!window.caches) return;
    caches.open(DC).then(function(c){
      return c.keys().then(function(reqs){
        reqs.forEach(function(q){
          var name = decodeURIComponent(new URL(q.url).pathname).replace(/^.*?__data\//, '');
          if (name !== 'index.json' && !keep[name]) c.delete(q);
        });
      });
    }).catch(function(){});
  }
  function getPaper(e){
    return cacheGet(e.file).then(function(c){
      if (c && c.hash === e.hash) return c;
      if (!source){ if (c) return c; throw new Error('no source'); }
      return source.get(e.file).then(function(b){
        cachePut(e.file, b); setHave(e.file, b.hash); return b;
      }, function(err){ if (c) return c; throw err; });
    });
  }

  // ---- 목록 ----
  function fileId(e){ return String(e.file || '').replace(/^papers\//, '').replace(/\.json$/, ''); }
  function entryOf(id){
    if (!INDEX) return null;
    for (var i = 0; i < INDEX.papers.length; i++) if (fileId(INDEX.papers[i]) === id) return INDEX.papers[i];
    return null;
  }
  function fmtDate(sec){
    var d = new Date(sec * 1000);
    try { return d.toLocaleString(md4Lang, {dateStyle: 'medium', timeStyle: 'short'}); } catch (e) { return d.toLocaleString(); }
  }
  function el(tag, cls, text){ var n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function renderLib(note){
    var have = haveMap(), n = 0;
    libList.innerHTML = '';
    doc.getElementById('lib-h').textContent = INDEX ? md4T('논문 {n}편', {n: INDEX.papers.length}) : APP;
    (INDEX ? INDEX.papers : []).forEach(function(e){
      var off = have[e.file] === e.hash; if (off) n++;
      var li = el('li'), a = el('a');
      a.href = '#!' + fileId(e); a.setAttribute('data-open', fileId(e));
      var title = el('span', 'li-title', e.title_ko || e.title);
      if (off) title.appendChild(el('span', 'dl', '⤓'));
      a.appendChild(title);
      if (e.title_ko) a.appendChild(el('span', 'li-en', e.title));
      var meta = el('span', 'li-meta', [e.authors, e.year, e.venue].filter(function(x){ return x; }).join(' · '));
      meta.appendChild(el('span', 'badge', e.has_ko ? md4T('번역') : md4T('원문만')));
      a.appendChild(meta); li.appendChild(a); libList.appendChild(li);
    });
    if (INDEX && !INDEX.papers.length) libList.appendChild(el('li', 'empty', md4T('올린 논문이 없어요. 데스크톱 앱에서 폰으로 보내 주세요.')));
    libBar.innerHTML = '';
    var info = [];
    if (INDEX && INDEX.updated) info.push(md4T('{date}에 올린 목록', {date: fmtDate(INDEX.updated)}));
    if (INDEX && INDEX.papers.length) info.push(md4T('오프라인 {n}/{m}편', {n: n, m: INDEX.papers.length}));
    if (note) info.push(note);
    libBar.appendChild(el('span', 'grow', info.join(' · ')));
    if (source){
      var re = el('button', '', md4T('새로 고침')); re.onclick = function(){ loadIndex(true); };
      libBar.appendChild(re);
    }
    if (INDEX && n < INDEX.papers.length && source){
      var all = el('button', '', md4T('모두 오프라인에 저장')); all.onclick = function(){ saveAll(all); };
      libBar.appendChild(all);
    }
  }
  function saveAll(btn){
    var list = INDEX.papers.slice(), total = list.length, done = 0, failed = 0;
    try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {}
    btn.disabled = true;
    (function next(){
      if (!list.length){ renderLib(failed ? md4T('{n}편은 받지 못했어요', {n: failed}) : ''); return; }
      var e = list.shift();
      getPaper(e).catch(function(){ failed++; }).then(function(){
        done++; btn.textContent = md4T('저장 중… {n}/{m}', {n: done, m: total}); next();
      });
    })();
  }
  function addAct(pr){
    if (!pr || !pr.button) return;
    var b = el('button', '', pr.button); b.onclick = pr.act; libBar.appendChild(b);
  }
  function showIndex(ix, note, pr){
    INDEX = ix; renderLib(note); addAct(pr);
    if (!routed){ routed = true; route(); }
  }
  function loadIndex(force){
    if (!force) libBar.textContent = md4T('불러오는 중…');
    return cacheGet('index.json').then(function(cached){
      if (cached && !force) showIndex(cached);
      if (!source){
        if (!cached) libBar.textContent = md4T('연결된 저장소가 없어요.');
        return;
      }
      return source.get('index.json').then(function(ix){
        cachePut('index.json', ix); setUiLang(ix.ui_lang); pruneCache(ix); showIndex(ix);
      }, function(err){
        var pr = sourceProblem(err), offline = !pr.button && err && !err.kind;
        if (cached) showIndex(cached, offline ? md4T('오프라인 — 받아 둔 목록이에요') : pr.text, pr);
        else { libBar.innerHTML = ''; libBar.appendChild(el('span', 'grow', pr.text)); addAct(pr); }
      });
    });
  }

  // ---- 목록 ↔ 논문 ----
  function unmount(){ if (cur){ cur.parentNode.removeChild(cur); cur = null; } }
  function showLib(push){
    loadSeq++; unmount(); loading.style.display = 'none';
    lib.classList.remove('off'); topTitle.textContent = APP;
    closeSheet(); window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!lib');
  }
  function mount(b, target){
    main.insertAdjacentHTML('beforeend', b.html);
    cur = doc.getElementById(PID); cur.classList.add('on');
    DATA[PID] = {key: b.key, title: b.title_ko || b.title, cites: b.cites || {}, fns: b.fns || {}};
    var t = target ? doc.getElementById(target) : null, off = 0;
    if (!t){
      var saved = load('pos:' + b.key, '').split(':');
      t = saved[0] ? doc.getElementById(PID + '-' + saved[0]) : null; off = +saved[1] || 0;
    }
    if (t){ t.scrollIntoView({block: 'start'}); if (off) window.scrollBy(0, off); } else window.scrollTo(0, 0);
  }
  function openPaper(id, push, target){
    if (id === PID){ if (cur) window.scrollTo(0, 0); return !!cur; }
    var e = entryOf(id);
    if (!e) return false;
    var seq = ++loadSeq;
    unmount(); closeSheet();
    lib.classList.add('off'); topTitle.textContent = e.title_ko || e.title;
    loading.style.display = ''; loading.textContent = md4T('불러오는 중…');
    window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!' + id);
    getPaper(e).then(function(b){
      if (seq !== loadSeq) return;
      loading.style.display = 'none'; mount(b, target);
      if (INDEX) renderLib();
    }, function(err){
      if (seq !== loadSeq) return;
      if (err && err.kind === 'auth'){
        loading.textContent = md4T('Google Drive 연결이 만료됐어요.') + ' ';
        var b = el('button', '', md4T('다시 연결')); b.onclick = function(){ gSignIn(); };
        loading.appendChild(b);
      } else loading.textContent = md4T('이 논문을 받지 못했어요. 인터넷에 연결된 뒤 다시 열어 보세요.');
    });
    return true;
  }
  try { history.scrollRestoration = 'manual'; } catch (e) {}
  function route(){
    var h = location.hash.replace(/^#!?/, '');
    if (h && h !== 'lib' && openPaper(h, false)) return;
    showLib(false);
  }
  doc.getElementById('back').onclick = function(){ showLib(true); };
  window.addEventListener('popstate', route);
  libList.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a[data-open]') : null;
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    openPaper(a.getAttribute('data-open'), true);
  });
  var saveTimer = null;
  window.addEventListener('scroll', function(){
    if (!cur || saveTimer) return;
    saveTimer = setTimeout(function(){
      saveTimer = null;
      var r = topRow(); if (!r || !cur) return;
      var into = Math.max(0, Math.round(64 - r.getBoundingClientRect().top));
      store('pos:' + cur.getAttribute('data-key'), r.id.replace(/^p\d+-/, '') + ':' + into);
    }, 400);
  }, {passive: true});

  labels(); topTitle.textContent = APP;
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(function(){});
  loadIndex(false);
"""

_SW_JS = r"""
// 앱 껍데기 캐시 — 먼저 캐시로 띄우고 뒤에서 새로 받아 둔다. 데이터(data/·__data/)는 앱이 직접 캐시한다.
var V = '%(version)s', SHELL = 'narani-shell-' + V;
var FILES = ['./', 'index.html', 'app.js', 'manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png'];
self.addEventListener('install', function(e){
  e.waitUntil(caches.open(SHELL).then(function(c){ return c.addAll(FILES); }).then(function(){ return self.skipWaiting(); }));
});
self.addEventListener('activate', function(e){
  e.waitUntil(caches.keys().then(function(ks){
    return Promise.all(ks.filter(function(k){ return k.indexOf('narani-shell-') === 0 && k !== SHELL; })
      .map(function(k){ return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});
self.addEventListener('fetch', function(e){
  var req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  var scope = new URL(self.registration.scope).pathname, rel = url.pathname.slice(scope.length);
  if (/^(data|__data)\//.test(rel)) return;
  e.respondWith(caches.open(SHELL).then(function(c){
    var key = req.mode === 'navigate' ? './' : req;
    return c.match(key, {ignoreSearch: true}).then(function(hit){
      var net = fetch(req).then(function(r){ if (r.ok && (hit || req.mode === 'navigate')) c.put(key, r.clone()); return r; });
      if (hit){ net.catch(function(){}); return hit; }
      return net;
    });
  }));
});
"""


def _js_strings(*codes: str) -> set[str]:
    return {m.group("s") for code in codes for m in i18n._JS_CALL.finditer(code)}


def _catalogs() -> dict[str, dict[str, str]]:
    """화면 언어별 카탈로그 — 리더 JS가 쓰는 문구만 (전부 실으면 index.html이 커진다)."""
    used = _js_strings(mobile.CORE_JS, _APP_JS)
    out: dict[str, dict[str, str]] = {}
    for lang in i18n.available():
        if lang == i18n.SOURCE:
            continue
        cat = i18n.catalog(lang)
        out[lang] = {k: v for k, v in cat.items() if k in used}
    return out


def _icon_png(size: int) -> bytes:
    """원문|번역 두 쪽 — 단순한 아이콘 (PIL이 없으면 1×1)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # pragma: no cover — 데스크톱 의존성에 늘 있다
        return bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                             "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082")
    im = Image.new("RGB", (size, size), (47, 111, 208))
    d = ImageDraw.Draw(im)
    pad, gap, r = size * 0.22, size * 0.05, size * 0.05
    w = (size - 2 * pad - gap) / 2
    for i, fill in enumerate(((255, 255, 255), (222, 233, 252))):
        x0 = pad + i * (w + gap)
        d.rounded_rectangle((x0, pad, x0 + w, size - pad), radius=r, fill=fill)
        for k in range(4):  # 글줄
            y = pad + size * 0.1 + k * size * 0.1
            d.rounded_rectangle((x0 + w * 0.15, y, x0 + w * (0.85 if k < 3 else 0.55), y + size * 0.035),
                                radius=size * 0.015, fill=(47, 111, 208) if i == 0 else (90, 140, 220))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _index_html(cfg: dict) -> str:
    payload = json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c")
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">"
        "<meta name=\"color-scheme\" content=\"light dark\">"
        "<meta name=\"theme-color\" content=\"#fbfaf7\" media=\"(prefers-color-scheme: light)\">"
        "<meta name=\"theme-color\" content=\"#161615\" media=\"(prefers-color-scheme: dark)\">"
        "<meta name=\"mobile-web-app-capable\" content=\"yes\">"
        "<meta name=\"apple-mobile-web-app-capable\" content=\"yes\">"
        f"<meta name=\"apple-mobile-web-app-title\" content=\"{APP_NAME}\">"
        "<link rel=\"manifest\" href=\"manifest.webmanifest\">"
        "<link rel=\"apple-touch-icon\" href=\"icon-180.png\">"
        f"<title>{APP_NAME}</title><style>{mobile._CSS}{_PWA_CSS}</style>"
        f"<script>window.NARANI_CFG = {payload};</script></head><body>"
        "<input type=\"radio\" name=\"mode\" id=\"m-ko\" class=\"mode\" checked>"
        "<input type=\"radio\" name=\"mode\" id=\"m-en\" class=\"mode\">"
        "<input type=\"radio\" name=\"mode\" id=\"m-both\" class=\"mode\">"
        "<header class=\"top\">"
        "<button class=\"only-js back\" id=\"back\">☰</button>"
        f"<span class=\"top-title\" id=\"top-title\">{APP_NAME}</span>"
        "<span class=\"seg\"><label for=\"m-ko\" id=\"lbl-ko\"></label><label for=\"m-en\" id=\"lbl-en\"></label>"
        "<label for=\"m-both\" id=\"lbl-both\"></label></span>"
        "<button class=\"only-js fs\" id=\"fs-dn\"></button><button class=\"only-js fs\" id=\"fs-up\"></button>"
        "</header>"
        f"<main><nav id=\"lib\"><h1 id=\"lib-h\">{APP_NAME}</h1><div class=\"lib-bar\" id=\"lib-bar\"></div>"
        "<ol id=\"lib-list\"></ol></nav><p id=\"loading\" style=\"display:none\"></p></main>"
        "<div id=\"sheet\" role=\"dialog\" aria-live=\"polite\"><div class=\"sh-grip\"></div>"
        "<div class=\"sh-label\"></div><div class=\"sh-text\"></div><div class=\"sh-acts\"></div></div>"
        "<script src=\"app.js\"></script></body></html>"
    )


def site_files(*, local: bool = False, google_client_id: str = "") -> dict[str, tuple[bytes, str]]:
    """웹앱 파일들 → {이름: (바이트, MIME)}. local=True면 같은 주소의 `data/`를 저장소로 쓴다(미리보기)."""
    cfg = {"app_name": APP_NAME, "source_lang": i18n.SOURCE, "fallback_lang": "en", "local": local,
           "google_client_id": google_client_id, "catalogs": _catalogs()}
    app_js = _PRELUDE_JS + "(function(){\n" + sentpair.JS + mobile.CORE_JS + _APP_JS + "})();\n"
    manifest = {
        "name": APP_NAME, "short_name": APP_NAME, "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#fbfaf7", "theme_color": "#fbfaf7",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }
    files: dict[str, tuple[bytes, str]] = {
        "index.html": (_index_html(cfg).encode("utf-8"), "text/html; charset=utf-8"),
        "app.js": (app_js.encode("utf-8"), "text/javascript; charset=utf-8"),
        "manifest.webmanifest": (json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
                                 "application/manifest+json"),
        "icon-180.png": (_icon_png(180), "image/png"),
        "icon-192.png": (_icon_png(192), "image/png"),
        "icon-512.png": (_icon_png(512), "image/png"),
    }
    # 껍데기가 바뀌면 서비스 워커도 바뀌어야 새 판을 받는다 — 내용 지문을 캐시 이름에
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + files[name][0])
    files["sw.js"] = ((_SW_JS % {"version": h.hexdigest()[:12]}).encode("utf-8"), "text/javascript; charset=utf-8")
    return files


def write_site(out: Path, **kw: object) -> list[Path]:
    """웹앱 파일을 폴더에 쓴다 (정적 호스팅에 올릴 것)."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (data, _mime) in site_files(**kw).items():  # type: ignore[arg-type]
        (out / name).write_bytes(data)
        written.append(out / name)
    return written


def make_server(data_dir: Path, port: int = 0, host: str = "127.0.0.1",
                google_client_id: str = "") -> ThreadingHTTPServer:
    """미리보기 서버 — 웹앱 + `data/`에 로컬 저장소 폴더. 기본은 127.0.0.1에만 붙는다(시험용)."""
    # Google 클라이언트가 있으면 Drive가 저장소다(로컬 data/는 그대로 두지만 앱은 쓰지 않는다)
    files = site_files(local=not google_client_id, google_client_id=google_client_id)
    root = Path(data_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:  # 조용히
            pass

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
            if path.startswith("data/"):
                target = (root / path[5:]).resolve()
                if root not in target.parents or not target.is_file():
                    self.send_error(404)
                    return
                body = target.read_bytes()
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            elif path in files:
                body, mime = files[path]
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_background(data_dir: Path, port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread]:
    srv = make_server(data_dir, port)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, th
