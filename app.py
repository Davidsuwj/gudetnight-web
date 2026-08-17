# -*- coding: utf-8 -*-
"""股得Night manual frontend (new interface; does not modify Hermes cron jobs)."""
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
JOBS = BASE / "jobs"
JOBS.mkdir(exist_ok=True)
PYTHON = sys.executable
# Required only when the FastAPI process runs in WSL but the worker must run in Windows.
WIN_BASE = os.environ.get("GUDETNIGHT_WINDOWS_PROJECT_DIR", str(BASE))

app = FastAPI(title="股得Night 上傳控制台")
app.mount("/jobs", StaticFiles(directory=str(JOBS)), name="jobs")

STAGES = ["script", "audio", "video", "upload", "comment"]


def parse_product_json(raw: str) -> dict | None:
    text = str(raw or "").strip()
    if not text:
        return None
    from affiliate_product import normalize_product
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"product_json is not valid JSON: {exc.msg}") from exc
    return normalize_product(payload)


def job_dir(job_id: str) -> Path:
    d = JOBS / job_id
    if not d.exists():
        raise HTTPException(404, "job not found")
    return d


def state_path(job_id: str) -> Path:
    return job_dir(job_id) / "state.json"


def load_state(job_id: str) -> dict:
    with open(state_path(job_id), "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(job_id: str, data: dict):
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(state_path(job_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_jobs():
    rows = []
    for p in sorted(JOBS.glob("*/state.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p, "r", encoding="utf-8") as f:
                rows.append(json.load(f))
        except Exception:
            pass
    return rows


def running(job_id: str) -> bool:
    st = load_state(job_id)
    pid = st.get("pid")
    if not pid:
        return False
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(int(pid))
    except Exception:
        # Windows fallback
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in r.stdout


def start_worker(job_id: str, stage: str):
    st = load_state(job_id)
    if st.get("running"):
        raise HTTPException(409, "job is already running")
    log_path = job_dir(job_id) / "worker.log"
    if os.name == "nt":
        cmd = [PYTHON, str(BASE / "worker.py"), "--job", job_id, "--stage", stage]
    else:
        ps = f"cd {WIN_BASE}; python.exe worker.py --job {job_id} --stage {stage}"
        cmd = ["powershell.exe", "-NoProfile", "-Command", ps]
    with open(log_path, "a", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=str(BASE), stdout=log, stderr=subprocess.STDOUT)
    st["running"] = True
    st["pid"] = proc.pid
    st["current_stage"] = stage
    st["status"] = f"running_{stage}"
    save_state(job_id, st)


def status_badge(st: dict) -> str:
    s = st.get("status", "new")
    cls = "ok" if s in ("done", "uploaded") or s.startswith("needs_") else "warn" if s.startswith("running") else "err" if s == "failed" else "muted"
    return f'<span class="badge {cls}">{s}</span>'


def artifact_local_path(path: str) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if p.exists():
        return p
    # A Windows worker can write drive-letter paths; translate them for the WSL server.
    m = path.replace("\\", "/")
    if len(m) > 2 and m[1] == ":":
        drive = m[0].lower()
        q = Path(f"/mnt/{drive}" + m[2:])
        if q.exists():
            return q
    return None


HTML_HEAD = """
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>股得Night 上傳控制台</title>
<style>
:root{--bg:#0b1020;--card:#141a2e;--muted:#8ea0c6;--text:#edf3ff;--line:#26314f;--pri:#7c5cff;--ok:#27c08a;--warn:#f6b44b;--err:#ff5c7a}*{box-sizing:border-box}body{margin:0;font-family:"Microsoft JhengHei",system-ui,sans-serif;background:linear-gradient(135deg,#09111f,#151032);color:var(--text)}.wrap{max-width:1180px;margin:0 auto;padding:28px}h1{margin:0 0 18px;font-size:32px}.grid{display:grid;grid-template-columns:1.1fr .9fr;gap:18px}.card{background:rgba(20,26,46,.92);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 10px 30px #0005}.field{margin:14px 0}label{display:block;color:#cbd6f6;margin-bottom:8px;font-weight:700}textarea,input,select{width:100%;border:1px solid #344061;border-radius:12px;background:#0d1326;color:var(--text);padding:12px;font-size:15px}textarea{min-height:180px}.row{display:flex;gap:10px;flex-wrap:wrap}.btn{display:inline-block;border:0;border-radius:12px;padding:11px 15px;background:var(--pri);color:white;text-decoration:none;font-weight:800;cursor:pointer}.btn.secondary{background:#26314f}.btn.ok{background:var(--ok)}.btn.warn{background:var(--warn);color:#111}.btn.err{background:var(--err)}.badge{border-radius:99px;padding:4px 9px;font-size:12px;font-weight:900}.badge.ok{background:#143c34;color:#42e6ad}.badge.warn{background:#4b3510;color:#ffd78a}.badge.err{background:#511725;color:#ff91a6}.badge.muted{background:#29324d;color:#b7c3e6}.job{padding:12px;border-bottom:1px solid var(--line)}.job a{color:#d8d2ff;text-decoration:none;font-weight:800}.small{color:var(--muted);font-size:13px}.pre{white-space:pre-wrap;background:#080d1b;border:1px solid var(--line);border-radius:14px;padding:14px;max-height:420px;overflow:auto}.kv{display:grid;grid-template-columns:130px 1fr;gap:8px;margin:10px 0}.stage{padding:10px;border:1px solid var(--line);border-radius:12px;margin:8px 0}.stage.done{border-color:#2a7a60}.stage.active{border-color:#b08b3b}
</style></head><body><div class="wrap">
"""
HTML_FOOT = "</div></body></html>"


@app.get("/", response_class=HTMLResponse)
def index():
    jobs = list_jobs()
    job_html = "".join(
        f'<div class="job"><a href="/job/{j["id"]}">{j.get("title") or j["id"]}</a> {status_badge(j)}<div class="small">{j.get("target_type")} · {j.get("approval_mode")} · {j.get("created_at")}</div></div>'
        for j in jobs[:30]
    ) or '<div class="small">目前沒有任務</div>'
    return HTML_HEAD + f"""
<h1>🌙 股得Night 上傳控制台</h1>
<div class="grid">
  <div class="card">
    <h2>新增手動任務</h2>
    <form method="post" action="/submit">
      <div class="field"><label>Input Prompt（給 GPT 的需求）</label><textarea name="prompt" required placeholder="例如：今天想做台積電法說會後的台股盤後觀察，語氣偏短影音、重點放 AI 供應鏈... "></textarea></div>
      <div class="row">
        <div class="field" style="flex:1"><label>上傳類型</label><select name="target_type"><option value="youtube">YouTube 一般影片（維持原 podcast 流程）</option><option value="shorts">YouTube Shorts（CONTEXT1/2/3 各切兩段 → GPT 生成 6 張圖 + 原 TTS 合成）</option></select></div>
        <div class="field" style="flex:1"><label>審核模式</label><select name="approval_mode"><option value="manual">各階段給我檢查後才下一步</option><option value="auto">Auto Approve 全自動跑到上傳</option></select></div>
      </div>
      <div class="field"><label>圖片 System Prompt（選填，給 GPT 生圖的語氣/風格指示）</label><textarea name="img_prompt" placeholder="例如：台灣財經短影音風格、插畫風、不要人物、不要內嵌文字、深色背景、科技感 ..." style="min-height:60px"></textarea></div>
      <div class="field"><label>Amazon 商品 JSON（選填；排程會自動填入官網查核資料與 SiteStripe 分潤短鏈）</label><textarea name="product_json" placeholder='{{"asin":"...","name":"...","amazon_url":"https://www.amazon.com/dp/...","affiliate_url":"https://amzn.to/...","price":"$...","popularity_evidence":"...","relevance_reason":"..."}}' style="min-height:90px"></textarea></div>
      <button class="btn" type="submit">建立任務</button>
    </form>
  </div>
  <div class="card"><h2>最近任務</h2>{job_html}</div>
</div>
""" + HTML_FOOT


@app.post("/submit")
async def submit(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    form = {k: v[0] if v else "" for k, v in parse_qs(raw, keep_blank_values=True).items()}
    prompt = str(form.get("prompt", "")).strip()
    target_type = str(form.get("target_type", "youtube"))
    approval_mode = str(form.get("approval_mode", "manual"))
    img_prompt = str(form.get("img_prompt", "")).strip()
    try:
        product = parse_product_json(form.get("product_json", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not prompt:
        raise HTTPException(400, "prompt required")
    if target_type not in ("youtube", "shorts"):
        raise HTTPException(400, "bad target_type")
    if approval_mode not in ("auto", "manual"):
        raise HTTPException(400, "bad approval_mode")
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    d = JOBS / job_id
    d.mkdir(parents=True)
    st = {
        "id": job_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "prompt": prompt,
        "target_type": target_type,
        "approval_mode": approval_mode,
        "img_prompt": img_prompt,
        "product": product,
        "status": "queued",
        "running": False,
        "current_stage": None,
        "completed_stages": [],
        "title": "",
        "artifacts": {},
    }
    save_state(job_id, st)
    start_worker(job_id, "script")
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str):
    st = load_state(job_id)
    log_file = job_dir(job_id) / "worker.log"
    log = log_file.read_text(encoding="utf-8", errors="replace")[-12000:] if log_file.exists() else ""
    artifacts = st.get("artifacts", {})
    links = []
    for name, path in artifacts.items():
        local = artifact_local_path(path)
        if local:
            try:
                rel = local.resolve().relative_to(JOBS.resolve())
            except Exception:
                rel = None
            if rel:
                links.append(f'<a class="btn secondary" href="/jobs/{rel.as_posix()}" target="_blank">下載/預覽 {name}</a>')
            else:
                links.append(f'<span class="small">{name}: {path}</span>')
    stage_html = ""
    done = set(st.get("completed_stages", []))
    for s in STAGES:
        cls = "done" if s in done else "active" if st.get("current_stage") == s and st.get("running") else ""
        stage_html += f'<div class="stage {cls}"><b>{s}</b> {"✅" if s in done else ""}</div>'
    next_stage = st.get("next_stage")
    controls = ""
    if st.get("running"):
        controls = '<span class="badge warn">執行中，頁面可重新整理看進度</span>'
    elif next_stage:
        controls = f'<form method="post" action="/job/{job_id}/approve" style="display:inline"><button class="btn ok" type="submit">確認目前產出，執行下一步：{next_stage}</button></form>'
    elif st.get("status") in ("done", "uploaded"):
        controls = '<span class="badge ok">流程完成</span>'
    elif st.get("status") == "failed":
        cur = st.get("current_stage") or "script"
        controls = f'<form method="post" action="/job/{job_id}/retry" style="display:inline"><button class="btn warn" type="submit">重跑目前階段：{cur}</button></form>'
    return HTML_HEAD + f"""
<div class="row" style="justify-content:space-between;align-items:center"><h1>任務 {job_id}</h1><a class="btn secondary" href="/">回首頁</a></div>
<div class="grid">
  <div class="card">
    <h2>{st.get('title') or '尚未產生標題'} {status_badge(st)}</h2>
    <div class="kv"><div class="small">類型</div><div>{st.get('target_type')}</div><div class="small">審核</div><div>{st.get('approval_mode')}</div><div class="small">圖片風格提示</div><div class="small" style="word-break:break-all">{st.get('img_prompt') or '（未設定）'}</div><div class="small">YouTube URL</div><div>{st.get('youtube_url') or '-'}</div></div>
    <h3>階段</h3>{stage_html}
    <div class="row">{controls}{''.join(links)}</div>
    <h3>GPT JSON / 文案</h3><div class="pre">{json.dumps(st.get('script_json', {}), ensure_ascii=False, indent=2)[:8000]}</div>
  </div>
  <div class="card">
    <h2>Log</h2><div class="pre">{log}</div>
  </div>
</div>
<script>setTimeout(()=>location.reload(), 8000)</script>
""" + HTML_FOOT


@app.post("/job/{job_id}/approve")
def approve(job_id: str):
    st = load_state(job_id)
    nxt = st.get("next_stage")
    if not nxt:
        raise HTTPException(400, "no next stage")
    start_worker(job_id, nxt)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.post("/job/{job_id}/retry")
def retry(job_id: str):
    st = load_state(job_id)
    stage = st.get("current_stage") or "script"
    st["running"] = False
    st["pid"] = None
    save_state(job_id, st)
    start_worker(job_id, stage)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    return JSONResponse(load_state(job_id))
