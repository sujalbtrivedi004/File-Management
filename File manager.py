"""
File Management Program (Python only, zero dependencies)

Run:   python file_manager.py
Stack: http.server (backend) + sqlite3 (database) + embedded HTML/CSS/JS (frontend)
Features: drag & drop upload, search, category filters, star, rename, delete,
          image previews, grid/list views, dark/light theme, responsive layout.
"""
import json, mimetypes, os, sqlite3, threading, time, uuid, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE, "storage")
DB = os.path.join(BASE, "driftbox.db")
PORT = int(os.environ.get("PORT", 8000))
MAX_BYTES = 200 * 1024 * 1024

CATS = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"},
    "document": {"pdf", "doc", "docx", "txt", "md", "xls", "xlsx", "ppt", "pptx", "csv", "rtf"},
    "audio": {"mp3", "wav", "ogg", "flac", "m4a"},
    "video": {"mp4", "mov", "avi", "mkv", "webm"},
    "archive": {"zip", "rar", "7z", "tar", "gz"},
    "code": {"py", "js", "ts", "html", "css", "json", "java", "c", "cpp", "go", "rs", "sh", "sql"},
}


def category(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return next((c for c, exts in CATS.items() if ext in exts), "other")


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init():
    os.makedirs(STORE, exist_ok=True)
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, stored TEXT NOT NULL,
            size INTEGER, mime TEXT, category TEXT, starred INTEGER DEFAULT 0, created REAL)""")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code=200, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def fid(self):
        try: return int(urlparse(self.path).path.rsplit("/", 1)[-1])
        except ValueError: return -1

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path); p = u.path; q = parse_qs(u.query)
        if p == "/": return self.send(200, PAGE, "text/html; charset=utf-8")
        if p == "/api/files":
            sql, args = "SELECT * FROM files WHERE 1=1", []
            cat = q.get("cat", ["all"])[0]
            if cat == "starred": sql += " AND starred=1"
            elif cat != "all": sql += " AND category=?"; args.append(cat)
            if q.get("q", [""])[0]: sql += " AND name LIKE ?"; args.append(f"%{q['q'][0]}%")
            sort = {"name": "name COLLATE NOCASE", "size": "size DESC"}.get(q.get("sort", [""])[0], "created DESC")
            with db() as con:
                return self.send(200, [dict(r) for r in con.execute(sql + " ORDER BY " + sort, args)])
        if p == "/api/stats":
            with db() as con:
                counts = {r["category"]: r["n"] for r in con.execute("SELECT category, COUNT(*) n FROM files GROUP BY category")}
                tot = con.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) s, COALESCE(SUM(starred),0) st FROM files").fetchone()
            return self.send(200, {"counts": counts, "all": tot["n"], "size": tot["s"], "starred": tot["st"]})
        if p.startswith("/file/"):
            with db() as con:
                r = con.execute("SELECT * FROM files WHERE id=?", (self.fid(),)).fetchone()
            if not r: return self.send(404, {"error": "File not found"})
            with open(os.path.join(STORE, r["stored"]), "rb") as f: data = f.read()
            disp = "inline" if "inline" in q else "attachment"
            return self.send(200, data, r["mime"] or "application/octet-stream",
                             {"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(r['name'])}"})
        self.send(404, {"error": "Not found"})

    # ---------- POST ----------
    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/upload":
            name = os.path.basename(unquote(self.headers.get("X-Filename", "untitled"))) or "untitled"
            n = int(self.headers.get("Content-Length", 0))
            if n > MAX_BYTES: return self.send(413, {"error": "File is larger than 200 MB"})
            stored = uuid.uuid4().hex
            with open(os.path.join(STORE, stored), "wb") as f: f.write(self.rfile.read(n))
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            with db() as con:
                cur = con.execute("INSERT INTO files(name,stored,size,mime,category,created) VALUES(?,?,?,?,?,?)",
                                  (name, stored, n, mime, category(name), time.time()))
            return self.send(201, {"id": cur.lastrowid, "name": name})
        if p.startswith("/api/star/"):
            with db() as con: con.execute("UPDATE files SET starred=1-starred WHERE id=?", (self.fid(),))
            return self.send(200, {"ok": True})
        if p.startswith("/api/rename/"):
            name = os.path.basename(str(self.body_json().get("name", "")).strip())
            if not name: return self.send(400, {"error": "Name can't be empty"})
            with db() as con:
                con.execute("UPDATE files SET name=?, category=?, mime=? WHERE id=?",
                            (name, category(name), mimetypes.guess_type(name)[0] or "application/octet-stream", self.fid()))
            return self.send(200, {"ok": True})
        self.send(404, {"error": "Not found"})

    # ---------- DELETE ----------
    def do_DELETE(self):
        with db() as con:
            r = con.execute("SELECT stored FROM files WHERE id=?", (self.fid(),)).fetchone()
            if not r: return self.send(404, {"error": "File not found"})
            con.execute("DELETE FROM files WHERE id=?", (self.fid(),))
        try: os.remove(os.path.join(STORE, r["stored"]))
        except OSError: pass
        self.send(200, {"ok": True})


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Driftbox</title>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#120f26;--bg2:#1c1740;--card:rgba(255,255,255,.06);--line:rgba(255,255,255,.12);--tx:#f1eeff;--mute:#a59fc9;
--a:#8b5cf6;--b:#22d3ee;--star:#fbbf24;--danger:#fb7185;--r:20px;--shadow:0 20px 50px -20px rgba(0,0,0,.6)}
[data-theme=light]{--bg:#f3f0ff;--bg2:#e4ddff;--card:rgba(255,255,255,.75);--line:rgba(60,40,140,.14);--tx:#1d1646;--mute:#6a6394;--shadow:0 20px 40px -22px rgba(70,40,160,.35)}
*{box-sizing:border-box;margin:0}
body{font-family:Sora,system-ui,sans-serif;color:var(--tx);min-height:100vh;
background:radial-gradient(900px 500px at 85% -10%,rgba(139,92,246,.35),transparent),radial-gradient(700px 500px at -5% 105%,rgba(34,211,238,.22),transparent),linear-gradient(160deg,var(--bg),var(--bg2));background-attachment:fixed}
button,input{font:inherit;color:inherit}button{cursor:pointer}
:focus-visible{outline:2px solid var(--b);outline-offset:2px}
.app{display:grid;grid-template-columns:260px 1fr;min-height:100vh}
aside{padding:28px 20px;border-right:1px solid var(--line);backdrop-filter:blur(18px);position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:22px}
.logo{font-weight:800;font-size:1.5rem;letter-spacing:-.03em;display:flex;align-items:center;gap:10px}
.logo i{width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-style:normal;box-shadow:0 8px 20px -6px var(--a)}
nav{display:flex;flex-direction:column;gap:4px;overflow:auto}
nav button{display:flex;align-items:center;gap:12px;padding:11px 14px;border:0;background:none;border-radius:14px;text-align:left;color:var(--mute);transition:.2s}
nav button:hover{background:var(--card);color:var(--tx)}
nav button.on{background:linear-gradient(90deg,rgba(139,92,246,.35),rgba(34,211,238,.15));color:var(--tx);box-shadow:inset 0 0 0 1px var(--line)}
nav b{margin-left:auto;font-size:.75rem;font-weight:600;background:var(--card);padding:2px 9px;border-radius:99px}
.meter{margin-top:auto;padding:16px;border-radius:var(--r);background:var(--card);border:1px solid var(--line)}
.meter small{color:var(--mute)}.bar{height:8px;border-radius:9px;background:var(--line);margin:10px 0;overflow:hidden}
.bar span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--a),var(--b));transition:width .6s}
main{padding:28px clamp(16px,4vw,48px);min-width:0}
.top{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:24px}
.menu{display:none}
.search{flex:1;min-width:180px;display:flex;align-items:center;gap:10px;padding:0 18px;border-radius:99px;background:var(--card);border:1px solid var(--line);backdrop-filter:blur(14px)}
.search input{flex:1;border:0;background:none;padding:14px 0;outline:0}
.btn{border:1px solid var(--line);background:var(--card);padding:12px 18px;border-radius:99px;transition:.2s;backdrop-filter:blur(14px)}
.btn:hover{transform:translateY(-2px)}
.btn.pri{border:0;color:#fff;font-weight:600;background:linear-gradient(135deg,var(--a),#6366f1 60%,var(--b));box-shadow:0 12px 26px -10px var(--a)}
select.btn{appearance:none;padding-right:18px}select.btn option{color:#222}
.drop{border:2px dashed var(--line);border-radius:28px;padding:clamp(26px,5vw,48px);text-align:center;margin-bottom:28px;transition:.25s;
background:linear-gradient(120deg,rgba(139,92,246,.14),rgba(34,211,238,.1));position:relative;overflow:hidden}
.drop.over{border-color:var(--b);transform:scale(1.015);background:linear-gradient(120deg,rgba(139,92,246,.35),rgba(34,211,238,.25))}
.drop h2{font-size:clamp(1.3rem,3vw,1.9rem);letter-spacing:-.03em;margin:8px 0 4px}.drop p{color:var(--mute);font-size:.9rem}
.drop .ico{font-size:2.4rem;display:inline-block;animation:float 3s ease-in-out infinite}
@keyframes float{50%{transform:translateY(-8px)}}
.prog{height:5px;border-radius:9px;background:var(--line);margin:16px auto 0;max-width:340px;display:none;overflow:hidden}
.prog span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--a),var(--b));transition:width .3s}
.head{display:flex;align-items:baseline;gap:12px;margin-bottom:16px}.head h1{font-size:1.4rem;letter-spacing:-.02em}.head span{color:var(--mute);font-size:.85rem}
.files{display:grid;gap:18px;grid-template-columns:repeat(auto-fill,minmax(210px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;box-shadow:var(--shadow);backdrop-filter:blur(14px);display:flex;flex-direction:column;transition:.25s}
.card:hover{transform:translateY(-5px);border-color:var(--a)}
.thumb{height:130px;display:grid;place-items:center;font-size:3rem;background-size:cover;background-position:center}
.info{padding:14px 16px 8px;min-width:0}.info strong{display:block;font-weight:600;font-size:.92rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.info small{color:var(--mute)}
.acts{display:flex;gap:4px;padding:4px 10px 12px}
.acts button{border:0;background:none;padding:7px 9px;border-radius:10px;color:var(--mute);transition:.15s}
.acts button:hover{background:var(--line);color:var(--tx)}.acts .del:hover{color:var(--danger)}.acts .st.on{color:var(--star)}
.list .files{grid-template-columns:1fr;gap:10px}.list .card{flex-direction:row;align-items:center}
.list .thumb{height:60px;width:70px;font-size:1.6rem;flex:none}.list .info{flex:1}.list .acts{padding:0 12px}
.t-image{background-color:rgba(34,211,238,.25)}.t-document{background-color:rgba(139,92,246,.3)}.t-audio{background-color:rgba(244,114,182,.28)}
.t-video{background-color:rgba(251,146,60,.28)}.t-archive{background-color:rgba(251,191,36,.25)}.t-code{background-color:rgba(52,211,153,.25)}.t-other{background-color:rgba(148,163,184,.25)}
.empty{grid-column:1/-1;text-align:center;color:var(--mute);padding:50px 10px}
#toast{position:fixed;bottom:24px;left:50%;transform:translate(-50%,120px);background:var(--bg2);border:1px solid var(--line);padding:13px 22px;border-radius:99px;box-shadow:var(--shadow);transition:.35s;z-index:9}
#toast.show{transform:translate(-50%,0)}
@media(max-width:860px){.app{grid-template-columns:1fr}
aside{position:fixed;inset:0 auto 0 0;width:270px;z-index:8;transform:translateX(-105%);transition:.3s;background:var(--bg)}
aside.open{transform:none}.menu{display:inline-block}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<div class="app">
<aside id="side">
  <div class="logo"><i>&#9729;</i>Driftbox</div>
  <nav id="nav"></nav>
  <div class="meter"><small>Storage used</small><div class="bar"><span id="bar"></span></div><strong id="used">0 B</strong> <small id="cnt"></small></div>
</aside>
<main>
  <div class="top">
    <button class="btn menu" id="menu" aria-label="Open menu">&#9776;</button>
    <label class="search">&#128269;<input id="q" placeholder="Search your files" aria-label="Search files"></label>
    <select class="btn" id="sort" aria-label="Sort files"><option value="new">Newest</option><option value="name">Name</option><option value="size">Size</option></select>
    <button class="btn" id="view" aria-label="Toggle layout">&#9638; Grid</button>
    <button class="btn" id="theme" aria-label="Toggle theme">&#9680;</button>
    <button class="btn pri" id="pick">Upload files</button>
    <input type="file" id="file" multiple hidden>
  </div>
  <div class="drop" id="drop"><span class="ico">&#128230;</span><h2>Drop files anywhere here</h2><p>or use Upload files. Up to 200 MB each.</p><div class="prog" id="prog"><span></span></div></div>
  <div class="head"><h1 id="title">All files</h1><span id="sub"></span></div>
  <div id="wrap"><div class="files" id="files"></div></div>
</main></div><div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const CATS=[["all","&#127968;","All files"],["starred","&#11088;","Starred"],["image","&#128444;","Images"],["document","&#128196;","Documents"],["audio","&#127925;","Audio"],["video","&#127916;","Videos"],["archive","&#128476;","Archives"],["code","&#128187;","Code"],["other","&#128193;","Other"]];
const ICON=Object.fromEntries(CATS.map(c=>[c[0],c[1]]));
let S={cat:"all",q:"",sort:"new",view:localStorage.view||"grid"};
const esc=s=>s.replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=b=>{const u=["B","KB","MB","GB"];let i=0;while(b>=1024&&i<3){b/=1024;i++}return b.toFixed(i?1:0)+" "+u[i]};
function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("show");clearTimeout(t.h);t.h=setTimeout(()=>t.classList.remove("show"),2600)}
async function api(u,o){const r=await fetch(u,o);const j=await r.json();if(!r.ok)throw new Error(j.error||"Something went wrong");return j}
async function stats(){
  const s=await api("/api/stats");
  $("#nav").innerHTML=CATS.map(([k,i,l])=>{const n=k=="all"?s.all:k=="starred"?s.starred:(s.counts[k]||0);
    return `<button data-c="${k}" class="${S.cat==k?"on":""}"><span>${i}</span>${l}<b>${n}</b></button>`}).join("");
  $("#used").textContent=fmt(s.size);$("#cnt").textContent=`in ${s.all} file${s.all==1?"":"s"}`;
  $("#bar").style.width=Math.min(100,s.size/(1024**3)*100+(s.all?3:0))+"%";
}
async function load(){
  const f=await api(`/api/files?cat=${S.cat}&q=${encodeURIComponent(S.q)}&sort=${S.sort}`);
  $("#title").textContent=CATS.find(c=>c[0]==S.cat)[2];$("#sub").textContent=f.length+" shown";
  $("#files").innerHTML=f.length?f.map(x=>`<article class="card">
    <div class="thumb t-${x.category}" style="${x.category=="image"&&x.mime!="image/svg+xml"?`background-image:url(/file/${x.id}?inline=1)`:""}">${x.category=="image"?"":ICON[x.category]}</div>
    <div class="info"><strong title="${esc(x.name)}">${esc(x.name)}</strong><small>${fmt(x.size)} &middot; ${new Date(x.created*1000).toLocaleDateString()}</small></div>
    <div class="acts">
      <button class="st ${x.starred?"on":""}" data-a="star" data-id="${x.id}" aria-label="Star">${x.starred?"&#9733;":"&#9734;"}</button>
      <button data-a="ren" data-id="${x.id}" data-n="${esc(x.name)}" aria-label="Rename">&#9998;</button>
      <button onclick="location='/file/${x.id}'" aria-label="Download">&#11015;</button>
      <button class="del" data-a="del" data-id="${x.id}" aria-label="Delete">&#128465;</button></div></article>`).join("")
    :`<div class="empty"><h2>${S.q?"No matches":"Nothing here yet"}</h2><p>${S.q?"Try a different search.":"Drop a file above to get started."}</p></div>`;
  $("#wrap").className=S.view=="list"?"list":"";$("#view").innerHTML=S.view=="list"?"&#9776; List":"&#9638; Grid";
}
const refresh=()=>Promise.all([stats(),load()]);
async function upload(list){
  const arr=[...list];if(!arr.length)return;const p=$("#prog");p.style.display="block";let ok=0;
  for(const [i,f] of arr.entries()){
    try{await api("/api/upload",{method:"POST",headers:{"X-Filename":encodeURIComponent(f.name)},body:f});ok++}catch(e){toast(f.name+": "+e.message)}
    p.firstElementChild.style.width=((i+1)/arr.length*100)+"%";
  }
  setTimeout(()=>{p.style.display="none";p.firstElementChild.style.width=0},500);
  if(ok)toast(`Uploaded ${ok} file${ok>1?"s":""}`);refresh();
}
$("#nav").onclick=e=>{const b=e.target.closest("button");if(!b)return;S.cat=b.dataset.c;$("#side").classList.remove("open");refresh()};
$("#files").onclick=async e=>{const b=e.target.closest("[data-a]");if(!b)return;const id=b.dataset.id,a=b.dataset.a;
  try{
    if(a=="star")await api("/api/star/"+id,{method:"POST"});
    if(a=="del"){if(!confirm("Delete this file? This can't be undone."))return;await api("/api/files/"+id,{method:"DELETE"});toast("Deleted")}
    if(a=="ren"){const n=prompt("New name",b.dataset.n);if(!n)return;await api("/api/rename/"+id,{method:"POST",body:JSON.stringify({name:n})});toast("Renamed")}
    refresh();
  }catch(err){toast(err.message)}};
let tm;$("#q").oninput=e=>{clearTimeout(tm);tm=setTimeout(()=>{S.q=e.target.value;load()},200)};
$("#sort").onchange=e=>{S.sort=e.target.value;load()};
$("#view").onclick=()=>{S.view=S.view=="grid"?"list":"grid";localStorage.view=S.view;load()};
$("#theme").onclick=()=>{const d=document.documentElement;d.dataset.theme=d.dataset.theme=="light"?"dark":"light";localStorage.theme=d.dataset.theme};
$("#menu").onclick=()=>$("#side").classList.toggle("open");
$("#pick").onclick=()=>$("#file").click();$("#file").onchange=e=>{upload(e.target.files);e.target.value=""};
const dz=$("#drop");
["dragenter","dragover"].forEach(v=>addEventListener(v,e=>{e.preventDefault();dz.classList.add("over")}));
["dragleave","drop"].forEach(v=>addEventListener(v,e=>{e.preventDefault();if(v=="drop"||e.target===document.documentElement)dz.classList.remove("over")}));
addEventListener("drop",e=>upload(e.dataTransfer.files));
document.documentElement.dataset.theme=localStorage.theme||"dark";
refresh().catch(e=>toast(e.message));
</script></body></html>"""

if __name__ == "__main__":
    init()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Driftbox running at http://localhost:{PORT}  (Ctrl+C to stop)")
    threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nBye!")