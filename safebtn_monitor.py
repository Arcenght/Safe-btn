#!/usr/bin/env python3
"""
safebtn_monitor.py - cliente BLE do SafeBtn para o PC (Linux/BlueZ)

- Procura o botao "SafeBtn", conecta e assina os eventos (funciona tanto com
  firmware de notificacao quanto de indication)
- Decodifica cada evento (toque / longo)
- Descarta duplicados (o botao reenvia se a confirmacao se perder)
- Reconecta sozinho se o link cair
- Gancho on_event(): e aqui que depois entra o backend (ntfy, SMS...)

Uso:
    pip install bleak
    python3 safebtn_monitor.py
    python3 safebtn_monitor.py --address AA:BB:CC:DD:EE:FF
    python3 safebtn_monitor.py --webhook http://localhost:8000/evento

Payload do botao (6 bytes, little-endian): tipo(u8) seq(u8) uptime_s(u32)
"""

import argparse
import asyncio
import json
import struct
import time
import urllib.request
from collections import deque
from datetime import datetime

from bleak import BleakClient, BleakScanner

DEVICE_NAME = "SafeBtn"
SVC_UUID = "5a1b0001-7c3d-4e8f-9a2b-1c4d5e6f7a80"
EVT_UUID = "5a1b0002-7c3d-4e8f-9a2b-1c4d5e6f7a80"

EVT_NAMES = {1: "TOQUE", 2: "LONGO"}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def decode(data: bytes):
    """Retorna (tipo, seq, uptime_s) ou None se o tamanho estiver errado."""
    if len(data) != 6:
        return None
    return struct.unpack("<BBI", bytes(data))


async def post_webhook(url: str, payload: dict) -> None:
    def _send():
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()

    try:
        await asyncio.get_running_loop().run_in_executor(None, _send)
        log(f"webhook ok -> {url}")
    except Exception as e:  # nao derruba o monitor se o backend cair
        log(f"webhook FALHOU ({e})")


async def on_event(evt: dict, webhook: str | None) -> None:
    """Gancho do backend. Por enquanto so imprime (e chama webhook, se houver)."""
    log(f">>> EVENTO {evt['nome']}  seq={evt['seq']}  uptime_botao={evt['uptime_s']}s")
    if webhook:
        await post_webhook(webhook, evt)


async def find_device(address: str | None):
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=10)
    return await BleakScanner.find_device_by_filter(
        lambda d, ad: ad.local_name == DEVICE_NAME
        or SVC_UUID in (ad.service_uuids or []),
        timeout=10,
    )


async def run(address: str | None, webhook: str | None) -> None:
    seen = deque(maxlen=64)  # chaves (tipo, seq, uptime) ja processadas
    loop = asyncio.get_running_loop()
    backoff = 1

    def handler(_char, data: bytearray) -> None:
        d = decode(data)
        if d is None:
            log(f"payload invalido: {bytes(data).hex()}")
            return
        tipo, seq, uptime = d
        key = (tipo, seq, uptime)
        if key in seen:
            log(f"(duplicado ignorado: tipo={tipo} seq={seq})")
            return
        seen.append(key)
        evt = {
            "tipo": tipo,
            "nome": EVT_NAMES.get(tipo, f"DESCONHECIDO({tipo})"),
            "seq": seq,
            "uptime_s": uptime,
            "recebido_em": time.time(),
        }
        loop.create_task(on_event(evt, webhook))

    while True:
        try:
            log("procurando o SafeBtn...")
            dev = await find_device(address)
            if dev is None:
                log("nao encontrado, tentando de novo")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)
                continue

            disconnected = asyncio.Event()
            async with BleakClient(
                dev, disconnected_callback=lambda _c: disconnected.set()
            ) as client:
                log(f"conectado a {dev.address}")
                await client.start_notify(EVT_UUID, handler)
                log("assinado. Aperte o botao (Ctrl+C para sair)")
                backoff = 1
                await disconnected.wait()
            log("desconectado")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"erro: {e}")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 10)


def main() -> None:
    ap = argparse.ArgumentParser(description="Monitor BLE do SafeBtn")
    ap.add_argument("--address", help="endereco BLE do botao (opcional)")
    ap.add_argument("--webhook", help="URL que recebe cada evento via POST JSON")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.address, args.webhook))
    except KeyboardInterrupt:
        log("encerrado")


if __name__ == "__main__":
    main()
