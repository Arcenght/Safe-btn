#!/usr/bin/env python3
"""Simula o botao: manda um evento ao backend, sem Bluetooth.

    python simular.py toque
    python simular.py longo
    python simular.py toque --lat -23.55 --lon -46.63

Le URL e token de config.json (ou use --url / --token)."""
import argparse, json, random, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("tipo", choices=["toque", "longo"])
ap.add_argument("--url"); ap.add_argument("--token")
ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
a = ap.parse_args()

try:
    cfg = json.load(open("config.json", encoding="utf-8"))
except OSError:
    cfg = {}
url = (a.url or "http://localhost:%s" % cfg.get("port", 8000)).rstrip("/") + "/evento"
token = a.token or cfg.get("device_token", "")

body = {"tipo": 1 if a.tipo == "toque" else 2,
        "seq": random.randint(0, 255), "uptime_s": random.randint(1, 10**6)}
if a.lat is not None and a.lon is not None:
    body.update(lat=a.lat, lon=a.lon)

req = urllib.request.Request(url, data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json", "X-Token": token})
print(urllib.request.urlopen(req, timeout=10).read().decode())
