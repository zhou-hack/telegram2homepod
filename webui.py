"""
webui.py — Web 控制面板

Token 认证方式（三选一）：
  1. URL 参数:   http://host:8080/?token=xxx
  2. HTTP header: X-Token: xxx
  3. 登录表单:   首次访问没有 token 时显示输入框，填入后存 localStorage
"""

import logging
from aiohttp import web

import airplay
import downloader
import history
import queue_manager
from config import WEBUI_HOST, WEBUI_PORT, WEBUI_TOKEN, HTTP_HOST

logger = logging.getLogger(__name__)
_runner: web.AppRunner | None = None


# ── 鉴权 ──

def _check_token(request: web.Request) -> bool:
    token = (
        request.headers.get("X-Token")
        or request.rel_url.query.get("token", "")
    )
    return token == WEBUI_TOKEN


def _require_token(handler):
    async def middleware(request: web.Request):
        if _check_token(request):
            return await handler(request)
        return web.json_response({"error": "unauthorized"}, status=401)
    return middleware


# ── API ──

async def api_state(request):
    vol = await airplay.get_volume()
    cur = queue_manager.current()
    return web.json_response({
        "status":  "paused" if queue_manager.is_paused() else ("playing" if queue_manager.is_playing() else "stopped"),
        "current": cur,
        "queue":   queue_manager.queue_list(),
        "volume":  vol,
    })

async def api_history_list(request):
    return web.json_response({"items": list(reversed(history.get_all()))})

async def api_search(request):
    body = await request.json()
    q = body.get("q", "").strip()
    if not q:
        return web.json_response({"error": "empty query"}, status=400)
    songs = downloader.search_songs(q, limit=6)
    return web.json_response({"results": songs})

async def api_play(request):
    body = await request.json()
    video_id = body.get("video_id", "")
    artist   = body.get("artist", "")
    if not video_id:
        return web.json_response({"error": "missing video_id"}, status=400)
    try:
        item = await downloader.download(video_id, artist)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    queue_manager.add(item)
    return web.json_response({"ok": True, "title": item["title"]})

async def api_control(request):
    body = await request.json()
    action = body.get("action", "")
    if action == "pause":
        await queue_manager.pause()
    elif action == "resume":
        await queue_manager.resume()
    elif action == "stop":
        await queue_manager.stop_all()
    elif action == "next":
        ok = await queue_manager.skip_next()
        return web.json_response({"ok": ok})
    elif action == "prev":
        prev = await queue_manager.skip_prev()
        return web.json_response({"ok": prev is not None})
    else:
        return web.json_response({"error": f"unknown action: {action}"}, status=400)
    return web.json_response({"ok": True})

async def api_volume(request):
    body = await request.json()
    val = int(body.get("value", -1))
    if not 0 <= val <= 100:
        return web.json_response({"error": "0-100"}, status=400)
    ok = await airplay.set_volume(val)
    return web.json_response({"ok": ok})

async def api_history_play(request):
    n = int(request.match_info["n"])
    import os
    item = history.get_by_index(n)
    if not item:
        return web.json_response({"error": "index out of range"}, status=404)
    if not os.path.exists(item.get("file_path", "")):
        try:
            item = await downloader.download(item["video_id"], item.get("artist", ""))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    queue_manager.add(item)
    return web.json_response({"ok": True, "title": item["title"]})

async def api_clear(request):
    count = queue_manager.clear_resume_cache()
    return web.json_response({"ok": True, "deleted": count})


# ── HTML ──

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HomePod</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=DM+Sans:wght@300;400;500;600&display=swap');
:root{
  --bg:#0d0d0f;--surface:#141416;--border:#222226;
  --accent:#ff6b35;--accent2:#ff9a6c;
  --text:#e8e8ea;--muted:#666670;--success:#3ddc84;
  --mono:'DM Mono',monospace;--sans:'DM Sans',sans-serif;--r:10px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh}

/* ── Login overlay ── */
#loginOverlay{
  position:fixed;inset:0;background:var(--bg);
  display:flex;align-items:center;justify-content:center;
  z-index:100;
}
#loginOverlay.hidden{display:none}
.login-box{
  background:var(--surface);border:1px solid var(--border);
  border-radius:16px;padding:36px 32px;width:340px;
  display:flex;flex-direction:column;gap:16px;
}
.login-box h2{font-family:var(--mono);font-size:14px;letter-spacing:.1em;color:var(--accent);text-transform:uppercase}
.login-box p{font-size:13px;color:var(--muted);line-height:1.5}
.login-input{
  background:var(--bg);border:1px solid var(--border);border-radius:var(--r);
  padding:10px 14px;color:var(--text);font-family:var(--mono);font-size:13px;
  outline:none;transition:border-color .2s;width:100%;
}
.login-input:focus{border-color:var(--accent)}
.login-btn{
  background:var(--accent);color:#fff;border:none;border-radius:var(--r);
  padding:11px;font-size:13px;font-weight:600;cursor:pointer;
  font-family:var(--sans);transition:background .15s;
}
.login-btn:hover{background:var(--accent2)}
.login-err{color:#e05;font-size:12px;font-family:var(--mono);min-height:16px}

/* ── App layout ── */
#app{display:grid;grid-template-columns:300px 1fr;grid-template-rows:52px 1fr;height:100vh}
#app.hidden{display:none}

header{
  grid-column:1/-1;display:flex;align-items:center;padding:0 20px;
  border-bottom:1px solid var(--border);gap:12px;
}
.logo{font-family:var(--mono);font-size:12px;letter-spacing:.12em;color:var(--accent);text-transform:uppercase;flex-shrink:0}
.dot{width:7px;height:7px;border-radius:50%;background:var(--muted);transition:background .3s;flex-shrink:0}
.dot.playing{background:var(--success);box-shadow:0 0 8px var(--success);animation:pulse 2s infinite}
.dot.paused{background:var(--accent)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.now-playing{flex:1;font-size:13px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.now-playing span{color:var(--text)}
.vol-wrap{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);font-family:var(--mono);flex-shrink:0}
input[type=range]{-webkit-appearance:none;width:76px;height:3px;background:var(--border);border-radius:2px;outline:none}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:var(--accent);cursor:pointer}

aside{border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.panel{border-bottom:1px solid var(--border);flex-shrink:0}
.panel.grow{flex:1;overflow:hidden;display:flex;flex-direction:column}
.panel-hd{padding:10px 14px;font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted);font-family:var(--mono)}

.controls{display:flex;align-items:center;justify-content:center;gap:6px;padding:10px 14px 14px}
.btn{display:flex;align-items:center;justify-content:center;border:none;cursor:pointer;transition:all .15s;font-family:var(--sans)}
.btn-c{width:34px;height:34px;border-radius:50%;background:var(--surface);border:1px solid var(--border);color:var(--text);font-size:13px}
.btn-c:hover{background:var(--border)}
.btn-c.primary{width:42px;height:42px;background:var(--accent);border-color:var(--accent);color:#fff;font-size:15px}
.btn-c.primary:hover{background:var(--accent2)}

.q-list{overflow-y:auto;flex:1;padding-bottom:8px}
.q-item{display:flex;align-items:center;gap:8px;padding:7px 14px;font-size:12px;border-bottom:1px solid #1a1a1d}
.q-item:hover{background:var(--surface)}
.q-idx{font-family:var(--mono);font-size:10px;color:var(--muted);width:16px;flex-shrink:0}
.q-title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.q-artist{color:var(--muted);font-size:11px;flex-shrink:0;max-width:70px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

main{display:flex;flex-direction:column;overflow:hidden}
.search-bar{padding:12px 18px;border-bottom:1px solid var(--border);display:flex;gap:8px}
.search-input{flex:1;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:8px 12px;color:var(--text);font-family:var(--sans);font-size:13px;outline:none;transition:border-color .2s}
.search-input:focus{border-color:var(--accent)}
.search-input::placeholder{color:var(--muted)}
.btn-search{background:var(--accent);color:#fff;border-radius:var(--r);padding:0 16px;font-size:13px;font-weight:500;border:none;cursor:pointer;transition:background .15s;flex-shrink:0}
.btn-search:hover{background:var(--accent2)}

.tabs{display:flex;border-bottom:1px solid var(--border);padding:0 18px}
.tab{padding:9px 14px;font-size:11px;letter-spacing:.05em;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;font-family:var(--mono)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab:hover:not(.active){color:var(--text)}

.content{flex:1;overflow-y:auto;padding:10px 18px}
.r-item{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:var(--r);cursor:default;transition:background .15s;border:1px solid transparent}
.r-item:hover{background:var(--surface);border-color:var(--border)}
.r-info{flex:1;overflow:hidden}
.r-title{font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.r-meta{font-size:11px;color:var(--muted);margin-top:2px}
.r-dur{font-family:var(--mono);font-size:11px;color:var(--muted);flex-shrink:0}
.r-acts{display:flex;gap:5px;opacity:0;transition:opacity .15s;flex-shrink:0}
.r-item:hover .r-acts{opacity:1}
.btn-sm{padding:4px 9px;border-radius:6px;font-size:11px;font-weight:500;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;transition:all .15s;white-space:nowrap}
.btn-sm.play{background:var(--accent);border-color:var(--accent);color:#fff}
.btn-sm:hover:not(.play){background:var(--border)}
.btn-sm.play:hover{background:var(--accent2)}
.empty{color:var(--muted);font-size:13px;text-align:center;padding:36px 0;font-family:var(--mono)}

#toast{position:fixed;bottom:20px;right:20px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:9px 14px;font-size:13px;opacity:0;transform:translateY(8px);transition:all .25s;pointer-events:none;z-index:200;max-width:250px}
#toast.show{opacity:1;transform:translateY(0)}
#toast.ok{border-color:var(--success);color:var(--success)}
#toast.err{border-color:#e05;color:#e05}
.spin{display:inline-block;width:13px;height:13px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
</style>
</head>
<body>

<!-- Login -->
<div id="loginOverlay">
  <div class="login-box">
    <h2>◉ HomePod</h2>
    <p>输入 Token 以访问控制面板</p>
    <input class="login-input" id="tokenInput" type="password" placeholder="Token…" onkeydown="if(event.key==='Enter')doLogin()">
    <button class="login-btn" onclick="doLogin()">进入</button>
    <div class="login-err" id="loginErr"></div>
  </div>
</div>

<!-- App -->
<div id="app" class="hidden">
<header>
  <div class="logo">◉ HomePod</div>
  <div class="dot" id="statusDot"></div>
  <div class="now-playing" id="nowPlaying">—</div>
  <div class="vol-wrap">
    <span>🔊</span>
    <input type="range" id="volSlider" min="0" max="100" value="50">
    <span id="volVal" style="width:28px">50%</span>
  </div>
</header>

<aside>
  <div class="panel">
    <div class="controls">
      <button class="btn btn-c" onclick="ctrl('prev')">⏮</button>
      <button class="btn btn-c" onclick="ctrl('stop')" style="color:#e05">⏹</button>
      <button class="btn btn-c primary" id="ppBtn" onclick="togglePlay()">▶</button>
      <button class="btn btn-c" onclick="ctrl('next')">⏭</button>
    </div>
  </div>
  <div class="panel grow">
    <div class="panel-hd" id="qHd">Queue</div>
    <div class="q-list" id="qList"><div class="empty">队列为空</div></div>
  </div>
</aside>

<main>
  <div class="search-bar">
    <input class="search-input" id="searchInput" placeholder="搜索歌曲 / 粘贴 YouTube 链接…" onkeydown="if(event.key==='Enter')doSearch()">
    <button class="btn btn-search" onclick="doSearch()">搜索</button>
  </div>
  <div class="tabs">
    <div class="tab active" id="tab-search" onclick="switchTab('search')">SEARCH</div>
    <div class="tab" id="tab-history" onclick="switchTab('history')">HISTORY</div>
  </div>
  <div class="content" id="content">
    <div class="empty">输入歌名或 YouTube 链接开始搜索</div>
  </div>
</main>
</div>

<div id="toast"></div>

<script>
// ── Token ──
let TOKEN = '';

function getStoredToken(){ return localStorage.getItem('homepod_token') || ''; }
function saveToken(t){ localStorage.setItem('homepod_token', t); }

function initAuth(){
  // 优先 URL ?token=
  const url = new URL(location.href);
  const urlToken = url.searchParams.get('token');
  if(urlToken){ TOKEN = urlToken; saveToken(urlToken); }
  else { TOKEN = getStoredToken(); }

  if(TOKEN){ verifyAndShow(); }
  else { showLogin(); }
}

function showLogin(){
  document.getElementById('loginOverlay').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
}

async function verifyAndShow(){
  // 试请求 /api/state 验证 token
  try{
    const res = await fetch('/api/state', { headers:{ 'X-Token': TOKEN } });
    if(res.status === 401){ saveToken(''); showLogin(); return; }
    document.getElementById('loginOverlay').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    startPolling();
  } catch(e){ showLogin(); }
}

async function doLogin(){
  const t = document.getElementById('tokenInput').value.trim();
  if(!t) return;
  TOKEN = t;
  const res = await fetch('/api/state', { headers:{ 'X-Token': TOKEN } });
  if(res.status === 401){
    document.getElementById('loginErr').textContent = 'Token 不正确';
    return;
  }
  saveToken(TOKEN);
  document.getElementById('loginErr').textContent = '';
  // 清掉 URL 里的 token（如果有）
  history.replaceState({}, '', location.pathname);
  document.getElementById('loginOverlay').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  startPolling();
}

// ── API ──
function hdr(){ return { 'Content-Type':'application/json', 'X-Token': TOKEN }; }

async function api(method, path, body){
  const res = await fetch(path, {
    method, headers: hdr(),
    body: body ? JSON.stringify(body) : undefined
  });
  if(res.status === 401){ saveToken(''); showLogin(); throw new Error('unauthorized'); }
  return res.json();
}

// ── Toast ──
let toastTmr;
function toast(msg, type='ok'){
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = 'show '+type;
  clearTimeout(toastTmr);
  toastTmr = setTimeout(()=> el.className='', 2500);
}

// ── Polling ──
let pollInterval;
function startPolling(){ pollState(); pollInterval = setInterval(pollState, 3000); }

async function pollState(){
  try{
    const d = await api('GET', '/api/state');
    updateHeader(d); updateQueue(d.queue, d.current);
  } catch(e){}
}

function updateHeader(d){
  document.getElementById('statusDot').className = 'dot '+d.status;
  const np = document.getElementById('nowPlaying');
  if(d.current?.title){
    const a = d.current.artist ? ` — ${d.current.artist}` : '';
    np.innerHTML = `<span>${esc(d.current.title)}</span>${esc(a)}`;
  } else { np.textContent = '—'; }
  document.getElementById('ppBtn').textContent = d.status==='playing' ? '⏸' : '▶';
  if(d.volume != null){
    document.getElementById('volSlider').value = d.volume;
    document.getElementById('volVal').textContent = d.volume+'%';
  }
}

function updateQueue(queue, current){
  const el = document.getElementById('qList');
  const total = queue.length + (current?.title ? 1 : 0);
  document.getElementById('qHd').textContent = 'Queue' + (total ? ' · '+total : '');
  if(!current?.title && !queue.length){ el.innerHTML='<div class="empty">队列为空</div>'; return; }
  let html = '';
  if(current?.title) html += `<div class="q-item"><div class="q-idx">▶</div><div class="q-title">${esc(current.title)}</div><div class="q-artist">${esc(current.artist||'')}</div></div>`;
  queue.forEach((item,i) => {
    html += `<div class="q-item"><div class="q-idx">${i+1}</div><div class="q-title">${esc(item.title)}</div><div class="q-artist">${esc(item.artist||'')}</div></div>`;
  });
  el.innerHTML = html;
}

// ── Controls ──
async function ctrl(action){
  try{ await api('POST','/api/control',{action}); pollState(); }
  catch(e){}
}
async function togglePlay(){
  const s = document.getElementById('statusDot').className;
  ctrl(s.includes('playing') ? 'pause' : 'resume');
}

// Volume debounce
let volTmr;
document.addEventListener('DOMContentLoaded', ()=>{
  document.getElementById('volSlider').addEventListener('input', e=>{
    const v = e.target.value;
    document.getElementById('volVal').textContent = v+'%';
    clearTimeout(volTmr);
    volTmr = setTimeout(()=> api('POST','/api/volume',{value:parseInt(v)}), 300);
  });
});

// ── Search ──
const YT = ['youtube.com','youtu.be','music.youtube.com'];
let currentTab = 'search';

function switchTab(tab){
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('tab-'+tab).classList.add('active');
  if(tab==='history') loadHistory();
}

async function doSearch(){
  const q = document.getElementById('searchInput').value.trim();
  if(!q) return;
  const c = document.getElementById('content');
  switchTab('search');

  if(YT.some(h=>q.includes(h))){
    c.innerHTML='<div class="empty"><span class="spin"></span> 下载中…</div>';
    try{
      const d = await api('POST','/api/play',{video_id:q,artist:''});
      if(d.ok){ toast('▶ '+d.title); c.innerHTML='<div class="empty">已加入队列</div>'; pollState(); }
      else { toast(d.error||'失败','err'); c.innerHTML='<div class="empty">下载失败</div>'; }
    } catch(e){}
    return;
  }

  c.innerHTML='<div class="empty"><span class="spin"></span> 搜索中…</div>';
  try{
    const d = await api('POST','/api/search',{q});
    renderResults(d.results||[]);
  } catch(e){ c.innerHTML='<div class="empty">搜索失败</div>'; }
}

function renderResults(results){
  const c = document.getElementById('content');
  if(!results.length){ c.innerHTML='<div class="empty">没有找到结果</div>'; return; }
  c.innerHTML = results.map(s=>`
    <div class="r-item">
      <div class="r-info">
        <div class="r-title">${esc(s.title)}</div>
        <div class="r-meta">${esc(s.artist)}</div>
      </div>
      <div class="r-dur">${s.duration}</div>
      <div class="r-acts">
        <button class="btn-sm play" onclick="playNow('${s.video_id}','${esc(s.artist)}',this)">▶ 播放</button>
        <button class="btn-sm" onclick="addQueue('${s.video_id}','${esc(s.artist)}',this)">+ 队列</button>
      </div>
    </div>`).join('');
}

async function playNow(vid,artist,btn){
  const orig = btn.textContent; btn.textContent='…';
  try{
    const d = await api('POST','/api/play',{video_id:vid,artist});
    if(d.ok){ toast('▶ '+d.title); pollState(); } else toast(d.error||'失败','err');
  } catch(e){}
  btn.textContent = orig;
}

async function addQueue(vid,artist,btn){
  const orig = btn.textContent; btn.textContent='…';
  try{
    const d = await api('POST','/api/play',{video_id:vid,artist});
    if(d.ok){ toast('+ '+d.title); pollState(); } else toast(d.error||'失败','err');
  } catch(e){}
  btn.textContent = orig;
}

// ── History ──
async function loadHistory(){
  const c = document.getElementById('content');
  c.innerHTML='<div class="empty"><span class="spin"></span></div>';
  try{
    const d = await api('GET','/api/history');
    const items = d.items||[];
    if(!items.length){ c.innerHTML='<div class="empty">暂无历史</div>'; return; }
    c.innerHTML = items.map((s,i)=>`
      <div class="r-item">
        <div class="r-info">
          <div class="r-title">${esc(s.title)}</div>
          <div class="r-meta">${esc(s.artist||'')}</div>
        </div>
        <div class="r-acts">
          <button class="btn-sm play" onclick="playHistory(${items.length-i},this)">▶ 重播</button>
        </div>
      </div>`).join('');
  } catch(e){ c.innerHTML='<div class="empty">加载失败</div>'; }
}

async function playHistory(n,btn){
  const orig = btn.textContent; btn.textContent='…';
  try{
    const d = await api('GET','/api/history/play/'+n);
    if(d.ok){ toast('▶ '+d.title); pollState(); } else toast(d.error||'失败','err');
  } catch(e){}
  btn.textContent = orig;
}

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// 启动
initAuth();
</script>
</body>
</html>
"""


# ── 路由 ──

async def serve_html(request: web.Request):
    return web.Response(text=HTML, content_type="text/html")


def _protected(handler):
    async def wrapper(request: web.Request):
        if not _check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)
    return wrapper


async def start():
    global _runner
    app = web.Application()
    app.router.add_get("/",                      serve_html)
    app.router.add_get("/api/state",             _protected(api_state))
    app.router.add_get("/api/history",           _protected(api_history_list))
    app.router.add_post("/api/search",           _protected(api_search))
    app.router.add_post("/api/play",             _protected(api_play))
    app.router.add_post("/api/control",          _protected(api_control))
    app.router.add_post("/api/volume",           _protected(api_volume))
    app.router.add_post("/api/clear",            _protected(api_clear))
    app.router.add_get("/api/history/play/{n}",  _protected(api_history_play))

    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, WEBUI_HOST, WEBUI_PORT)
    await site.start()
    logger.info(f"WebUI 启动: http://{HTTP_HOST}:{WEBUI_PORT}/?token={WEBUI_TOKEN}")


async def stop():
    global _runner
    if _runner:
        await _runner.cleanup()
        _runner = None
