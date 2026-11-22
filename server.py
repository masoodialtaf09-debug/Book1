"""Relay hosted server: auth (Supabase), saved runs, live progress, credits (Stripe).
Run locally with no setup (dev mode, in-memory):  uvicorn server:app --reload
"""
import hashlib, hmac, os, pathlib, tempfile, threading, time, uuid
from typing import List, Optional

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import relay_engine as eng

E = os.environ.get
SB_URL, SB_ANON, SB_SERVICE = E("SUPABASE_URL", "").rstrip("/"), E("SUPABASE_ANON_KEY", ""), E("SUPABASE_SERVICE_KEY", "")
STRIPE_KEY, STRIPE_WH, STRIPE_PRICE = E("STRIPE_SECRET_KEY", ""), E("STRIPE_WEBHOOK_SECRET", ""), E("STRIPE_PRICE_ID", "")
CREDITS_PER_PACK, BASE_URL = int(E("CREDITS_PER_PACK", "10")), E("BASE_URL", "http://localhost:8000").rstrip("/")
DEV = not SB_URL  # no Supabase configured: single user, in-memory, unlimited credits
NEEDS = {"facts", "code", "office", "math", "people", "money", "automate"}  # "docs" needs file upload, not offered here

app = FastAPI(title="Relay")
MEM = {}      # dev-mode runs
ACTIVE = {}   # user id -> run id (one run at a time per user)


def sb(method, path, **kw):
    h = {"apikey": SB_SERVICE, "Content-Type": "application/json", "Prefer": "return=representation"}
    if not SB_SERVICE.startswith("sb_secret_"):  # new secret keys go in apikey only; legacy JWT keys also go in Authorization
        h["Authorization"] = f"Bearer {SB_SERVICE}"
    r = requests.request(method, f"{SB_URL}/rest/v1/{path}", headers=h, timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.text else None


def user_id(authorization):
    if DEV:
        return "dev"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in first.")
    r = requests.get(f"{SB_URL}/auth/v1/user", headers={"apikey": SB_ANON, "Authorization": authorization}, timeout=15)
    if r.status_code != 200:
        raise HTTPException(401, "Your session expired. Sign in again.")
    return r.json()["id"]


def credits_of(uid):
    if DEV:
        return 999
    rows = sb("GET", f"profiles?id=eq.{uid}&select=credits")
    return rows[0]["credits"] if rows else 0


def spend(uid):
    return True if DEV else bool(sb("POST", "rpc/spend_credit", json={"uid": uid}))


def refund(uid, n=1):
    if not DEV:
        sb("POST", "rpc/add_credits", json={"uid": uid, "n": n})


def persist(run_id, **fields):
    if DEV:
        MEM[run_id].update(fields)
        return
    try:
        sb("PATCH", f"runs?id=eq.{run_id}", json=fields)
    except requests.RequestException:
        pass


class Project(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=1500)
    type: str = Field("general", max_length=60)
    audience: str = Field("", max_length=300)
    constraints: str = Field("", max_length=300)
    needs: List[str] = []
    rounds: int = Field(2, ge=1, le=3)


def worker(run_id, uid, project, rounds):
    events = []

    def emit(msg):
        events.append(str(msg))
        persist(run_id, events=list(events))

    token = eng._emit.set(emit)
    status, final = "failed", None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = eng.run(project, rounds, False, base=tmp)
            final = (pathlib.Path(out) / "final.md").read_text()
            status = "done"
    except (Exception, SystemExit) as e:
        events.append(f"Failed: {e}. Your credit was returned.")
        refund(uid)
    finally:
        eng._emit.reset(token)
        ACTIVE.pop(uid, None)
    persist(run_id, status=status, final=final, events=events)


@app.get("/api/config")
def config():
    return {"dev": DEV, "supabaseUrl": SB_URL, "anonKey": SB_ANON, "billing": bool(STRIPE_KEY and STRIPE_PRICE),
            "pack": CREDITS_PER_PACK}


@app.get("/api/me")
def me(authorization: Optional[str] = Header(None)):
    return {"credits": credits_of(user_id(authorization))}


@app.post("/api/runs")
def start_run(p: Project, authorization: Optional[str] = Header(None)):
    uid = user_id(authorization)
    if uid in ACTIVE:
        raise HTTPException(409, "A run is already in progress. Wait for it to finish.")
    if not spend(uid):
        raise HTTPException(402, "You are out of credits. Buy a pack to continue.")
    run_id = str(uuid.uuid4())
    if DEV:
        MEM[run_id] = {"id": run_id, "name": p.name, "status": "running", "events": [], "final": None, "created_at": time.time()}
    else:
        try:
            run_id = sb("POST", "runs", json={"user_id": uid, "name": p.name})[0]["id"]
        except requests.RequestException:
            refund(uid)
            raise HTTPException(503, "Could not save the run. Try again.")
    ACTIVE[uid] = run_id
    project = {"name": p.name, "goal": p.goal, "type": p.type, "audience": p.audience,
               "constraints": p.constraints, "needs": [n for n in p.needs if n in NEEDS]}
    threading.Thread(target=worker, args=(run_id, uid, project, p.rounds), daemon=True).start()
    return {"id": run_id}


@app.get("/api/runs")
def list_runs(authorization: Optional[str] = Header(None)):
    uid = user_id(authorization)
    if DEV:
        return sorted(({k: v[k] for k in ("id", "name", "status", "created_at")} for v in MEM.values()),
                      key=lambda r: r["created_at"], reverse=True)
    return sb("GET", f"runs?user_id=eq.{uid}&select=id,name,status,created_at&order=created_at.desc&limit=30")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, authorization: Optional[str] = Header(None)):
    uid = user_id(authorization)
    if DEV:
        r = MEM.get(run_id)
    else:
        rows = sb("GET", f"runs?id=eq.{run_id}&user_id=eq.{uid}&select=id,name,status,events,final")
        r = rows[0] if rows else None
    if not r:
        raise HTTPException(404, "Run not found.")
    return {k: r[k] for k in ("id", "name", "status", "events", "final")}


@app.post("/api/checkout")
def checkout(authorization: Optional[str] = Header(None)):
    uid = user_id(authorization)
    if not (STRIPE_KEY and STRIPE_PRICE):
        raise HTTPException(503, "Billing is not set up on this server.")
    r = requests.post("https://api.stripe.com/v1/checkout/sessions", auth=(STRIPE_KEY, ""), timeout=30, data={
        "mode": "payment", "line_items[0][price]": STRIPE_PRICE, "line_items[0][quantity]": 1,
        "success_url": BASE_URL + "/?paid=1", "cancel_url": BASE_URL + "/",
        "client_reference_id": uid, "metadata[credits]": CREDITS_PER_PACK})
    if r.status_code != 200:
        raise HTTPException(502, "Stripe could not start checkout.")
    return {"url": r.json()["url"]}


def stripe_signature_ok(body: bytes, header: str) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        t, sig = parts["t"], parts["v1"]
        if abs(time.time() - int(t)) > 300:
            return False
        mac = hmac.new(STRIPE_WH.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, sig)
    except (KeyError, ValueError):
        return False


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: Optional[str] = Header(None)):
    body = await request.body()
    if not STRIPE_WH or not stripe_signature or not stripe_signature_ok(body, stripe_signature):
        raise HTTPException(400, "Bad signature.")
    ev = __import__("json").loads(body)
    obj = ev.get("data", {}).get("object", {})
    if ev.get("type") == "checkout.session.completed" and obj.get("payment_status") == "paid" and not DEV:
        sb("POST", "rpc/add_credits_once", json={"sid": obj["id"], "uid": obj["client_reference_id"], "n": CREDITS_PER_PACK})
    return {"ok": True}


@app.get("/")
def index():
    here = pathlib.Path(__file__).parent
    page = here / "index.html"
    return FileResponse(page if page.exists() else here / "static" / "index.html")
