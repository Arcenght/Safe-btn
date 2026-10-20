#!/usr/bin/env python3
"""
SafeBtn backend

Recebe os eventos do botao (via app/script), avisa os contatos pelo ntfy e
escalona: toque -> contato 1 -> (sem confirmacao em ACK_TIMEOUT s) -> contato 2
-> ... -> se ninguem confirmar, avisa todos. Toque LONGO avisa todos de uma vez.

Rodar:
    pip install -r requirements.txt
    copie config.example.json para config.json e edite
    python server.py

Endpoints:
    POST /evento       (header X-Token)  {"tipo":1|2,"seq":N,"uptime_s":N,"lat":..,"lon":..}
    POST /localizacao  (header X-Token)  {"lat":..,"lon":..}
    GET  /ack/{token}                    pagina do contato com o botao Confirmar
    POST /ack/{token}                    confirma (para o escalonamento)
    GET  /status       (header X-Token)  incidentes recentes (depuracao)
"""

import asyncio
import html
import json
import os
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ----------------------------------------------------------------- config
CONFIG_PATH = os.environ.get("SAFEBTN_CONFIG", "config.json")
with open(CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)

PUBLIC_URL = cfg["public_url"].rstrip("/")
DEVICE_TOKEN = cfg["device_token"]
OWNER_NAME = cfg.get("owner_name", "O dono do botão")
OWNER_TOPIC = cfg.get("owner_topic")
NTFY_SERVER = cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
NTFY_TOKEN = cfg.get("ntfy_token", "")
ACK_TIMEOUT = float(cfg.get("ack_timeout_s", 120))
INCIDENT_WINDOW = float(cfg.get("incident_window_s", 900))
CONTACTS = cfg["contacts"]
LOG_FILE = cfg.get("log_file", "incidentes.jsonl")

EVT_TOQUE, EVT_LONGO = 1, 2
DEDUP_WINDOW = 600  # s: o botao reenvia se a confirmacao BLE se perder

# ----------------------------------------------------------------- estado


@dataclass
class Incident:
    id: str
    created: float
    lat: float | None = None
    lon: float | None = None
    kind_long: bool = False
    acked_by: str | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    notes: list = field(default_factory=list)


INCIDENTS: "OrderedDict[str, Incident]" = OrderedDict()
ACK_TOKENS: dict[str, tuple[str, str]] = {}  # token -> (incident_id, nome do contato)
SEEN: dict[tuple, float] = {}  # (tipo, seq, uptime) -> quando chegou
LAST_LOC: dict = {"lat": None, "lon": None, "t": 0.0}
TASKS: set = set()  # evita que as tarefas sejam coletadas pelo GC


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def note(inc: Incident, msg: str) -> None:
    inc.notes.append((time.time(), msg))
    log(f"[{inc.id}] {msg}")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "incidente": inc.id, "msg": msg},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def maps_link(inc: Incident) -> str | None:
    if inc.lat is None or inc.lon is None:
        return None
    return f"https://www.google.com/maps?q={inc.lat},{inc.lon}"


# ----------------------------------------------------------------- ntfy
async def ntfy_publish(topic: str, title: str, message: str, *, priority: int = 5,
                       tags: list | None = None, url: str | None = None,
                       action_label: str = "Confirmar") -> bool:
    """Publica no ntfy (API JSON, aceita acentos). Tenta 3 vezes."""
    body: dict = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": tags or ["rotating_light"],
    }
    if url:
        body["click"] = url
        body["actions"] = [{"action": "view", "label": action_label, "url": url}]
    headers = {"Authorization": f"Bearer {NTFY_TOKEN}"} if NTFY_TOKEN else {}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(NTFY_SERVER, json=body, headers=headers)
            if r.status_code < 300:
                return True
            log(f"ntfy respondeu {r.status_code} para {topic}")
        except Exception as e:
            log(f"ntfy falhou para {topic}: {e}")
        await asyncio.sleep(2 ** attempt)
    return False


async def notify_contact(inc: Incident, contact: dict, *, grave: bool, reason: str = "") -> bool:
    token = secrets.token_urlsafe(16)
    ACK_TOKENS[token] = (inc.id, contact["name"])
    url = f"{PUBLIC_URL}/ack/{token}"
    hora = datetime.now().strftime("%H:%M")
    loc = maps_link(inc)
    partes = []
    if grave:
        title = f"EMERGÊNCIA - {OWNER_NAME}"
        partes.append(f"{OWNER_NAME} SEGUROU o botão às {hora}: situação grave.")
        if reason:
            partes.append(reason)
        partes.append("Se não conseguir contato, ligue para o 190.")
    else:
        title = f"ALERTA - {OWNER_NAME}"
        partes.append(f"{OWNER_NAME} acionou o botão de segurança às {hora}.")
    partes.append(f"Local: {loc}" if loc else "Local: ainda indisponível.")
    partes.append("Toque em Confirmar para avisar que você vai ajudar.")
    ok = await ntfy_publish(contact["topic"], title, "\n".join(partes), url=url)
    note(inc, f"aviso a {contact['name']}: {'enviado' if ok else 'FALHOU'}"
              f"{' (grave)' if grave else ''}")
    return ok


async def notify_owner(inc: Incident, text: str) -> None:
    if OWNER_TOPIC:
        await ntfy_publish(OWNER_TOPIC, "SafeBtn", text, priority=4, tags=["bell"])


# ----------------------------------------------------------------- logica
async def go_long(inc: Incident, reason: str = "") -> None:
    """Escalada grave: avisa TODOS os contatos de uma vez."""
    if inc.kind_long:
        return
    inc.kind_long = True
    inc.event.set()  # acorda o escalonamento, que para
    note(inc, f"ESCALADA GRAVE: avisando todos os contatos {reason}".strip())
    await asyncio.gather(*(notify_contact(inc, c, grave=True, reason=reason)
                           for c in CONTACTS))
    await notify_owner(inc, "Alerta grave enviado a todos os seus contatos.")


async def escalate(inc: Incident) -> None:
    """Contato 1 -> espera -> contato 2 -> ... ; ao esgotar, avisa todos."""
    for contact in CONTACTS:
        if inc.acked_by or inc.kind_long:
            return
        ok = await notify_contact(inc, contact, grave=False)
        if not ok:
            note(inc, f"{contact['name']} inalcancavel, passando ao proximo")
            continue
        try:
            await asyncio.wait_for(inc.event.wait(), ACK_TIMEOUT)
        except asyncio.TimeoutError:
            note(inc, f"{contact['name']} nao confirmou em {ACK_TIMEOUT:.0f}s")
            continue
        return  # confirmado ou virou grave
    if not inc.acked_by and not inc.kind_long:
        note(inc, "ninguem confirmou")
        await go_long(inc, reason="Ninguém confirmou o primeiro aviso.")


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)


def open_incident() -> Incident | None:
    if not INCIDENTS:
        return None
    inc = next(reversed(INCIDENTS.values()))
    return inc if time.time() - inc.created < INCIDENT_WINDOW else None


def new_incident() -> Incident:
    inc = Incident(id=secrets.token_hex(3), created=time.time())
    if time.time() - LAST_LOC["t"] < 300:
        inc.lat, inc.lon = LAST_LOC["lat"], LAST_LOC["lon"]
    INCIDENTS[inc.id] = inc
    while len(INCIDENTS) > 50:
        INCIDENTS.popitem(last=False)
    return inc


# ----------------------------------------------------------------- API
app = FastAPI(title="SafeBtn backend")


class Evento(BaseModel):
    tipo: int
    seq: int = 0
    uptime_s: int = 0
    lat: float | None = None
    lon: float | None = None


class Localizacao(BaseModel):
    lat: float
    lon: float


def auth(x_token: str) -> None:
    if not secrets.compare_digest(x_token, DEVICE_TOKEN):
        raise HTTPException(status_code=401, detail="token invalido")


@app.post("/evento")
async def evento(e: Evento, x_token: str = Header(default="")):
    auth(x_token)
    if e.tipo not in (EVT_TOQUE, EVT_LONGO):
        raise HTTPException(status_code=400, detail="tipo desconhecido")

    now = time.time()
    for k in [k for k, t in SEEN.items() if now - t > DEDUP_WINDOW]:
        del SEEN[k]
    key = (e.tipo, e.seq, e.uptime_s)
    if key in SEEN:
        return {"status": "duplicado"}
    SEEN[key] = now

    if e.lat is not None and e.lon is not None:
        LAST_LOC.update(lat=e.lat, lon=e.lon, t=now)

    inc = open_incident()
    novo = inc is None
    if novo:
        inc = new_incident()
    if e.lat is not None and e.lon is not None:
        inc.lat, inc.lon = e.lat, e.lon

    if e.tipo == EVT_TOQUE:
        if not novo:
            note(inc, "toque repetido durante incidente em andamento (ignorado)")
            return {"status": "ja_em_andamento", "incidente": inc.id}
        note(inc, "TOQUE recebido: iniciando escalonamento")
        spawn(escalate(inc))
        return {"status": "iniciado", "incidente": inc.id}

    # LONGO
    if novo:
        note(inc, "LONGO recebido sem toque anterior")
    spawn(go_long(inc))
    return {"status": "escalada_grave", "incidente": inc.id}


@app.post("/localizacao")
async def localizacao(loc: Localizacao, x_token: str = Header(default="")):
    auth(x_token)
    LAST_LOC.update(lat=loc.lat, lon=loc.lon, t=time.time())
    inc = open_incident()
    if inc:
        inc.lat, inc.lon = loc.lat, loc.lon
    return {"status": "ok"}


def page(body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>SafeBtn</title><style>body{font-family:sans-serif;max-width:30em;"
        "margin:2em auto;padding:0 1em;font-size:1.15em}button{font-size:1.3em;"
        "padding:.8em 1.2em;width:100%;background:#c0392b;color:#fff;border:0;"
        "border-radius:.4em}a{color:#1a5fb4}</style></head><body>" + body + "</body></html>")


@app.get("/ack/{token}", response_class=HTMLResponse)
async def ack_page(token: str):
    if token not in ACK_TOKENS:
        return page("<h2>Link inválido ou expirado.</h2>")
    inc_id, name = ACK_TOKENS[token]
    inc = INCIDENTS.get(inc_id)
    if inc is None:
        return page("<h2>Alerta não encontrado.</h2>")
    loc = maps_link(inc)
    loc_html = (f"<p>📍 <a href='{html.escape(loc)}'>Ver localização no mapa</a></p>"
                if loc else "<p>Localização ainda indisponível.</p>")
    grave = ("<p><b>Situação grave.</b> Se não conseguir contato, ligue para o 190.</p>"
             if inc.kind_long else "")
    if inc.acked_by:
        return page(f"<h2>Alerta de {html.escape(OWNER_NAME)}</h2>{grave}{loc_html}"
                    f"<p>✅ Já confirmado por {html.escape(inc.acked_by)}.</p>")
    return page(
        f"<h2>Alerta de {html.escape(OWNER_NAME)}</h2>{grave}{loc_html}"
        f"<form method='post' action='/ack/{html.escape(token)}'>"
        "<button type='submit'>Confirmar: eu vou ajudar</button></form>")


@app.post("/ack/{token}", response_class=HTMLResponse)
async def ack(token: str):
    if token not in ACK_TOKENS:
        return page("<h2>Link inválido ou expirado.</h2>")
    inc_id, name = ACK_TOKENS[token]
    inc = INCIDENTS.get(inc_id)
    if inc is None:
        return page("<h2>Alerta não encontrado.</h2>")
    if inc.acked_by:
        return page(f"<h2>✅ Já confirmado por {html.escape(inc.acked_by)}.</h2>")
    inc.acked_by = name
    inc.event.set()
    note(inc, f"CONFIRMADO por {name}")
    spawn(notify_owner(inc, f"{name} confirmou que vai ajudar."))
    if inc.kind_long:  # avisa os demais, que ja tinham sido chamados
        for c in CONTACTS:
            if c["name"] != name:
                spawn(ntfy_publish(c["topic"], "SafeBtn",
                                   f"{name} confirmou que vai ajudar {OWNER_NAME}.",
                                   priority=3, tags=["white_check_mark"]))
    loc = maps_link(inc)
    extra = f"<p>📍 <a href='{html.escape(loc)}'>Ver localização no mapa</a></p>" if loc else ""
    return page("<h2>✅ Obrigado, confirmado.</h2>" + extra +
                "<p>Se a situação for grave, ligue para o 190.</p>")


@app.get("/status")
async def status(x_token: str = Header(default="")):
    auth(x_token)
    return [{"id": i.id, "idade_s": round(time.time() - i.created),
             "grave": i.kind_long, "confirmado_por": i.acked_by,
             "notas": [n for _, n in i.notes]} for i in INCIDENTS.values()]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=cfg.get("host", "0.0.0.0"), port=int(cfg.get("port", 8000)))
