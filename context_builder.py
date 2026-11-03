# -*- coding: utf-8 -*-
"""Context Builder: копирует выбранные файлы проекта в папку для чата.
Запуск: двойной клик на run.bat (или python context_builder.py)."""
import json, os, shutil, threading, webbrowser, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# ===================== НАСТРОЙКИ =====================
SOURCE_ROOT = r"C:\projects\my_project"   # папка проекта по умолчанию
TARGET_DIR = None   # None = папка output рядом со скриптом
PORT = 8765
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build"}
IGNORE_FILES = {".DS_Store", "Thumbs.db"}
MAX_FILES = 20000
PROJECT_PROMPT_NAME = "AGENTS_MAIN.md"      # главный промт проекта, в корень output
PRESET_PROMPT_NAME = "AGENTS.md"            # промт пресета, в папку пресета
# ======================================================

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE, "output")
DATA = os.path.join(OUTPUT, "_data")   # пресеты и настройки тоже в output
PRESETS = os.path.join(DATA, "presets")
SETTINGS = os.path.join(DATA, "settings.json")
os.makedirs(PRESETS, exist_ok=True)


def load_settings():
    s = {"source": SOURCE_ROOT, "target": TARGET_DIR or OUTPUT, "general_prompt": ""}
    if os.path.exists(SETTINGS):
        with open(SETTINGS, encoding="utf-8") as f:
            s.update(json.load(f))
    s["target"] = TARGET_DIR or OUTPUT   # целевая папка не запоминается между запусками
    return s


def save_settings(s):
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def preset_path(name):
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    if not safe:
        raise ValueError("Пустое имя пресета")
    return os.path.join(PRESETS, safe + ".json")


def list_presets():
    out = []
    for fn in sorted(os.listdir(PRESETS)):
        if fn.endswith(".json"):
            with open(os.path.join(PRESETS, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


count = 0


def build_tree(path):
    global count
    node = {"name": os.path.basename(path) or path, "children": []}
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return node
    for e in entries:
        if count > MAX_FILES:
            break
        if e.is_dir(follow_symlinks=False):
            if e.name in IGNORE_DIRS:
                continue
            node["children"].append(build_tree(e.path))
        elif e.name not in IGNORE_FILES:
            count += 1
            node["children"].append({"name": e.name})
    return node


def safe_rel(root, rel):
    full = os.path.normpath(os.path.join(root, rel))
    if os.path.commonpath([os.path.abspath(root), os.path.abspath(full)]) != os.path.abspath(root):
        raise ValueError("Путь вне проекта: " + rel)
    return full


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh",
     "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def translit(name):
    name = re.sub(r"[ыи]й", "iy", name.strip().lower())
    out = []
    for ch in name:
        low = ch.lower()
        if low in _TR:
            t = _TR[low]
            out.append(t)
        else:
            out.append(ch)
    r = re.sub(r"[^a-z0-9]+", "-", "".join(out)).strip("-")
    if not r:
        raise ValueError("Некорректное имя пресета")
    return r


def do_copy(d):
    root, target = d["source"], d["target"]
    files = d["files"]
    if not os.path.isdir(root):
        raise ValueError("Папка проекта не существует")
    if not target.strip():
        raise ValueError("Не указана папка вывода")
    ar, base = os.path.abspath(root), os.path.abspath(target)
    at = os.path.join(base, translit(d.get("preset_name", "")))
    if at == ar or ar.startswith(at + os.sep) or base == ar:
        raise ValueError("Папка вывода пресета пересекается с проектом")
    if d.get("clean") and os.path.isdir(at):
        for n in os.listdir(at):
            p = os.path.join(at, n)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
    os.makedirs(at, exist_ok=True)
    n = 0
    for rel in files:
        src = safe_rel(root, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(at, os.path.normpath(rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    gp, pp = d.get("general_prompt", "").strip(), d.get("preset_prompt", "").strip()
    if gp:
        with open(os.path.join(base, PROJECT_PROMPT_NAME), "w", encoding="utf-8") as f:
            f.write(gp + "\n")
    if pp:
        with open(os.path.join(at, PRESET_PROMPT_NAME), "w", encoding="utf-8") as f:
            f.write(pp + "\n")
    return {"copied": n, "general": bool(gp), "agents": bool(pp), "target": at}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        b = obj.encode("utf-8") if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        u = urlparse(self.path).path
        if u == "/":
            self.send(HTML, ctype="text/html")
        elif u == "/api/state":
            self.send({"settings": load_settings(), "presets": list_presets()})
        else:
            self.send({"error": "not found"}, 404)

    def do_POST(self):
        global count
        u = urlparse(self.path).path
        try:
            d = self.body()
            if u == "/api/tree":
                if not os.path.isdir(d["root"]):
                    raise ValueError("Папка не существует: " + d["root"])
                count = 0
                self.send({"tree": build_tree(d["root"]), "truncated": count > MAX_FILES})
            elif u == "/api/settings":
                s = load_settings()
                s.update({k: d[k] for k in ("source", "general_prompt") if k in d})
                save_settings(s)
                self.send({"ok": True})
            elif u == "/api/preset/save":
                p = {"name": d["name"].strip(), "paths": d["paths"], "prompt": d.get("prompt", "")}
                with open(preset_path(p["name"]), "w", encoding="utf-8") as f:
                    json.dump(p, f, ensure_ascii=False, indent=2)
                self.send({"ok": True})
            elif u == "/api/preset/delete":
                p = preset_path(d["name"])
                if os.path.exists(p):
                    os.remove(p)
                self.send({"ok": True})
            elif u == "/api/preset/merge":
                all_p = {p["name"]: p for p in list_presets()}
                sel = [all_p[n] for n in d["names"] if n in all_p]
                if len(sel) < 2:
                    raise ValueError("Выберите минимум два пресета")
                paths = []
                for p in sel:
                    for x in p["paths"]:
                        if x not in paths:
                            paths.append(x)
                prompt = "\n\n".join(p["prompt"].strip() for p in sel if p["prompt"].strip())
                np_ = {"name": d["name"].strip(), "paths": paths, "prompt": prompt}
                with open(preset_path(np_["name"]), "w", encoding="utf-8") as f:
                    json.dump(np_, f, ensure_ascii=False, indent=2)
                self.send({"ok": True})
            elif u == "/api/copy":
                self.send(do_copy(d))
            else:
                self.send({"error": "not found"}, 404)
        except Exception as e:
            self.send({"error": str(e)}, 400)


HTML = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Context Builder</title>
<style>
body{font:14px Segoe UI,Arial,sans-serif;margin:0;display:flex;height:100vh;color:#222}
#left{flex:2;padding:12px;overflow:auto;border-right:1px solid #ccc}
#right{flex:1;padding:12px;overflow:auto;min-width:340px;background:#f6f7f9}
input[type=text],textarea{width:100%;box-sizing:border-box;margin:2px 0 8px;padding:5px;font:inherit}
textarea{resize:vertical}
ul{list-style:none;padding-left:18px;margin:0}#tree>ul{padding-left:0}
.dir>span.n{font-weight:600;cursor:pointer}.tg{display:inline-block;width:14px;cursor:pointer;color:#666}
button{padding:5px 10px;margin:2px 2px 2px 0;cursor:pointer}
.pr{display:flex;align-items:center;gap:4px;padding:2px 0}.pr a{flex:1;cursor:pointer;color:#0645ad}
#msg{margin:6px 0;font-weight:600}.err{color:#c00}.ok{color:#080}
h3{margin:12px 0 4px}
</style></head><body>
<div id="left">
 <label>Папка проекта (абсолютный путь)</label>
 <div style="display:flex;gap:4px"><input type="text" id="source"><button onclick="loadTree()">Загрузить</button></div>
 <div><button onclick="setAll(true)">Выбрать всё</button><button onclick="setAll(false)">Снять всё</button>
 <span id="cnt"></span></div>
 <div id="tree"></div>
</div>
<div id="right">
 <label>Папка вывода (файлы пойдут в подпапку = транслит имени пресета)</label><input type="text" id="target">
 <label><input type="checkbox" id="clean" checked> Очистить папку пресета перед копированием</label>
 <h3>Общий промт проекта (output/AGENTS_MAIN.md)</h3><textarea id="gp" rows="4"></textarea>
 <h3>Промт пресета (AGENTS.md в папке пресета)</h3><textarea id="pp" rows="4"></textarea>
 <input type="text" id="pname" placeholder="Имя пресета">
 <button onclick="savePreset()">Сохранить пресет</button>
 <button onclick="copyNow()" style="font-weight:700">Копировать в целевую папку</button>
 <div id="msg"></div>
 <h3>Пресеты</h3><div id="presets"></div>
 <button onclick="mergePresets()">Создать новый из выбранных</button>
 <button onclick="delPresets()">Удалить выбранные</button>
</div>
<script>
const $=id=>document.getElementById(id);
let root=null, sel=new Set(), presets=[], nodes=[]; // nodes: файлы/папки с путём
async function api(u,d){const r=await fetch(u,{method:d?'POST':'GET',body:d?JSON.stringify(d):undefined});
 const j=await r.json(); if(!r.ok) throw new Error(j.error||'Ошибка'); return j;}
function msg(t,ok){const m=$('msg');m.textContent=t;m.className=ok?'ok':'err';}
function annotate(n,p){n.path=p; if(n.children) n.children.forEach(c=>annotate(c,p?p+'/'+c.name:c.name));}
function files(n,o=[]){if(n.children)n.children.forEach(c=>files(c,o));else o.push(n.path);return o;}
function state(n){const f=files(n);if(!f.length)return 0;const k=f.filter(x=>sel.has(x)).length;return k==0?0:k==f.length?2:1;}
function compact(n,o=[]){ // минимальный список путей
 if(!n.children){if(sel.has(n.path))o.push(n.path);return o;}
 if(n.path!==''&&state(n)==2){o.push(n.path);return o;}
 n.children.forEach(c=>compact(c,o));return o;}
function find(n,p){if(n.path===p)return n;if(n.children)for(const c of n.children){const r=find(c,p);if(r)return r;}return null;}
function render(){
 const t=$('tree');t.innerHTML='';if(!root)return;
 const ul=document.createElement('ul');root.children.forEach(c=>ul.appendChild(li(c)));t.appendChild(ul);
 $('cnt').textContent=' Выбрано файлов: '+sel.size;}
function li(n){
 const e=document.createElement('li');const cb=document.createElement('input');cb.type='checkbox';
 const s=state(n);cb.checked=s==2;cb.indeterminate=s==1;
 cb.onchange=()=>{files(n).forEach(f=>cb.checked?sel.add(f):sel.delete(f));refresh();};
 if(n.children){e.className='dir';const tg=document.createElement('span');tg.className='tg';tg.textContent=n.open?'▼':'▶';
 const nm=document.createElement('span');nm.className='n';nm.textContent=n.name;
 const tgl=()=>{n.open=!n.open;render();};tg.onclick=tgl;nm.onclick=tgl;
 e.append(tg,cb,' ',nm);
 if(n.open){const ul=document.createElement('ul');n.children.forEach(c=>ul.appendChild(li(c)));e.appendChild(ul);}
 }else{const sp=document.createElement('span');sp.className='tg';e.append(sp,cb,' ',n.name);}
 return e;}
function refresh(){// сохранить раскрытие при перерисовке — состояние хранится в узлах
 render();}
function setAll(v){sel=new Set(v?files(root):[]);render();}
async function loadTree(){
 try{const j=await api('/api/tree',{root:$('source').value});root=j.tree;annotate(root,'');root.open=true;
 sel=new Set();render();api('/api/settings',{source:$('source').value});
 msg(j.truncated?'Дерево усечено (слишком много файлов)':'Дерево загружено',!j.truncated);}
 catch(e){root=null;render();msg(e.message,false);}}
function applyPaths(paths){sel=new Set();if(!root)return;
 paths.forEach(p=>{const n=find(root,p);if(n)files(n).forEach(f=>sel.add(f));});render();}
function renderPresets(){const d=$('presets');d.innerHTML='';
 presets.forEach(p=>{const r=document.createElement('div');r.className='pr';
 r.innerHTML='<input type="checkbox" data-n=""><a></a>';r.firstChild.dataset.n=p.name;
 const a=r.querySelector('a');a.textContent=p.name+' ('+p.paths.length+')';
 a.onclick=()=>{applyPaths(p.paths);$('pp').value=p.prompt||'';$('pname').value=p.name;msg('Пресет загружен: '+p.name,true);};
 d.appendChild(r);});}
function checked(){return [...document.querySelectorAll('#presets input:checked')].map(i=>i.dataset.n);}
async function reloadPresets(){presets=(await api('/api/state')).presets;renderPresets();}
async function savePreset(){const name=$('pname').value.trim();if(!name)return msg('Введите имя пресета',false);
 try{await api('/api/preset/save',{name,paths:root?compact(root):[],prompt:$('pp').value});await reloadPresets();msg('Сохранено: '+name,true);}catch(e){msg(e.message,false);}}
async function mergePresets(){const names=checked();const name=$('pname').value.trim();
 if(names.length<2)return msg('Отметьте минимум два пресета',false);
 if(!name)return msg('Введите имя нового пресета в поле имени',false);
 try{await api('/api/preset/merge',{names,name});await reloadPresets();msg('Создан пресет: '+name,true);}catch(e){msg(e.message,false);}}
async function delPresets(){const names=checked();if(!names.length||!confirm('Удалить: '+names.join(', ')+'?'))return;
 for(const n of names)await api('/api/preset/delete',{name:n});await reloadPresets();}
async function copyNow(){
 if(!root)return msg('Сначала загрузите дерево',false);
 if(!$('pname').value.trim())return msg('Введите имя пресета (оно задаёт имя папки)',false);
 try{await api('/api/settings',{source:$('source').value,target:$('target').value,general_prompt:$('gp').value});
 const j=await api('/api/copy',{source:$('source').value,target:$('target').value,preset_name:$('pname').value,files:[...sel],clean:$('clean').checked,
 general_prompt:$('gp').value,preset_prompt:$('pp').value});
 msg('Скопировано файлов: '+j.copied+(j.agents?' + AGENTS.md':'')+(j.general?' + AGENTS_MAIN.md':'')+' → '+j.target,true);}catch(e){msg(e.message,false);}}
$('gp').onchange=()=>api('/api/settings',{general_prompt:$('gp').value});
(async()=>{const s=await api('/api/state');$('source').value=s.settings.source;$('target').value=s.settings.target;
 $('gp').value=s.settings.general_prompt;presets=s.presets;renderPresets();loadTree();})();
</script></body></html>"""


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = "http://127.0.0.1:%d/" % PORT
    print("Context Builder:", url, "(Ctrl+C для выхода)")
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
