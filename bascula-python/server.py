#!/usr/bin/env python3
"""
Servidor web para báscula PRENLIS 401C.
Solo biblioteca estándar de Python (sin pip).
El pesaje real lee el serial en una petición HTTP corta.
El panel de diagnóstico sigue usando Web Serial en Chrome.
"""

from __future__ import annotations

import json
import os
import pwd
import secrets
import subprocess
import sys
from datetime import datetime, timezone, timedelta
import grp
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import serial_io

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PESOS_FILE = DATA_DIR / "pesos.json"
HOST = os.environ.get("BASCULA_HOST", "127.0.0.1")
PORT = int(os.environ.get("BASCULA_PORT", "8080"))
TOKEN = os.environ.get("BASCULA_TOKEN", "")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}


def mexico_now_iso() -> str:
    tz = timezone(timedelta(hours=-6))
    return datetime.now(tz).isoformat()


def load_pesos() -> list[dict[str, Any]]:
    if not PESOS_FILE.is_file():
        return []
    try:
        data = json.loads(PESOS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_pesos(items: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PESOS_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _user_groups() -> list[str]:
    names: set[str] = set()
    for gid in os.getgroups():
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            pass
    return sorted(names)


def _processes_on_device(dev_path: str) -> list[str]:
    lines: list[str] = []
    try:
        proc = subprocess.run(
            ["fuser", "-v", dev_path],
            capture_output=True,
            text=True,
            timeout=3,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        for line in out.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return lines


def _device_entry(p: Path, groups: list[str], hints: list[str]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(p),
        "exists": p.exists(),
        "readable": os.access(p, os.R_OK),
        "writable": os.access(p, os.W_OK),
    }
    try:
        st = p.stat()
        item["mode"] = oct(st.st_mode)[-3:]
        item["owner"] = pwd.getpwuid(st.st_uid).pw_name
        dev_group = grp.getgrgid(st.st_gid).gr_name
        item["group"] = dev_group
        item["user_in_device_group"] = dev_group in groups
        if not item["writable"]:
            hints.append(
                "%s es %s:%s mode=%s — tu usuario NO puede escribir. "
                "Si el grupo es dialout: sudo usermod -aG dialout $USER "
                "y cierra sesión por completo. Temporal: sudo chmod a+rw %s"
                % (p, item["owner"], dev_group, item["mode"], p)
            )
    except OSError as exc:
        item["stat_error"] = str(exc)
    users = _processes_on_device(str(p))
    if users:
        item["fuser"] = users
        joined = " ".join(users).lower()
        if "chrome" in joined or "chromium" in joined:
            hints.append(
                "%s lo tiene Chrome (normal mientras la web está conectada). "
                "No ejecutes fuser -k: cerraría el puerto que acabas de abrir."
                % p.name
            )
        else:
            hints.append(
                "Hay procesos usando %s. Ciérralos o: sudo fuser -k %s" % (p.name, p)
            )
    return item


def serial_diagnostico() -> dict[str, Any]:
    groups = _user_groups()
    hints: list[str] = []
    in_dialout = "dialout" in groups
    in_plugdev = "plugdev" in groups

    if not in_dialout:
        hints.append(
            "Esta sesión todavía no tiene dialout (usermod no aplica hasta cerrar sesión). "
            "groups | grep dialout debe mostrar dialout tras reiniciar sesión. "
            "Mientras tanto, sudo chmod a+rw /dev/ttyUSB0 se pierde al desconectar el USB."
        )

    devices: list[dict[str, Any]] = []
    dev_dir = Path("/dev")
    usb_paths: list[Path] = []
    if dev_dir.is_dir():
        for pat in ("ttyUSB*", "ttyACM*"):
            usb_paths.extend(sorted(dev_dir.glob(pat)))
        for p in usb_paths:
            devices.append(_device_entry(p, groups, hints))

    can_access_serial = any(d.get("readable") and d.get("writable") for d in devices)

    if not usb_paths:
        hints.append(
            "No se encontró /dev/ttyUSB* ni /dev/ttyACM*. "
            "Revisa cable USB, lsusb, y desconecta/reconecta el adaptador FTDI."
        )

    modem = "unknown"
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "ModemManager"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        modem = (proc.stdout or proc.stderr or "").strip() or "unknown"
        if modem == "active":
            hints.append(
                "ModemManager está activo y a veces bloquea adaptadores USB-serial. "
                "Prueba: sudo systemctl stop ModemManager"
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    brltty = "unknown"
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "brltty"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        brltty = (proc.stdout or proc.stderr or "").strip() or "unknown"
        if brltty == "active":
            hints.append(
                "brltty puede capturar adaptadores CH340. "
                "Prueba desactivarlo temporalmente si el adaptador desaparece al conectar."
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    hints.append(
        "Cierra otras pestañas de Chrome que hayan usado el puerto, minicom, cutecom, etc."
    )
    hints.append(
        "En la web: Flow control = Ninguno; si falla open, desmarca DTR/RTS e intenta de nuevo."
    )

    primary = devices[0] if devices else None
    if primary and not primary.get("writable"):
        hints.insert(
            0,
            "Chrome falla con NetworkError code 19 porque el SO niega acceso a %s. "
            "Arregla permisos antes de reintentar Web Serial."
            % primary["path"],
        )

    # Quitar duplicados preservando orden
    seen: set[str] = set()
    unique_hints: list[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            unique_hints.append(h)

    return {
        "ok": True,
        "user": pwd.getpwuid(os.getuid()).pw_name,
        "uid": os.getuid(),
        "groups": groups,
        "in_dialout": in_dialout,
        "in_plugdev": in_plugdev,
        "can_access_serial": can_access_serial,
        "primary_device": primary,
        "adapter_note": "USB 0403:6015 = FTDI FT230X (tu adaptador en Chrome)",
        "devices": devices,
        "ModemManager": modem,
        "brltty": brltty,
        "hints": unique_hints,
    }


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Bascula-Token")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _texto(payload: dict[str, Any], clave: str, limite: int = 120) -> str:
    return str(payload.get(clave, "")).strip()[:limite]


def autorizado(handler: BaseHTTPRequestHandler) -> bool:
    if not TOKEN:
        return True
    recibido = handler.headers.get("X-Bascula-Token", "")
    return secrets.compare_digest(recibido, TOKEN)


class BasculaHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Bascula-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path.startswith("/api/") and not autorizado(self):
            json_response(self, 401, {"ok": False, "error": "Token inválido"})
            return

        if path == "/api/leer-peso":
            query = parse_qs(urlparse(self.path).query)
            plano = {k: v[0] for k, v in query.items() if v}
            self._leer_peso(plano, guardar=False)
            return

        if path == "/api/pesos":
            json_response(self, 200, {"ok": True, "pesos": load_pesos()})
            return

        if path == "/api/diagnostico":
            json_response(self, 200, serial_diagnostico())
            return

        if path == "/api/puertos":
            json_response(self, 200, {"ok": True, "puertos": serial_io.listar_puertos()})
            return

        if path in ("", "/"):
            path = "/index.html"

        file_path = (ROOT / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(ROOT)) or not file_path.is_file():
            self.send_error(404)
            return

        suffix = file_path.suffix.lower()
        content_type = MIME.get(suffix, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else "{}"
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _leer_peso(self, payload: dict[str, Any], guardar: bool) -> None:
        puerto = str(payload.get("puerto") or "/dev/ttyUSB0").strip()
        try:
            baudrate = int(payload.get("baudrate") or payload.get("baudRate") or 9600)
            bits = int(payload.get("data_bits") or payload.get("dataBits") or 8)
            paradas = int(payload.get("stop_bits") or payload.get("stopBits") or 1)
            ventana = float(payload.get("segundos") or 0.8)
        except (TypeError, ValueError):
            json_response(self, 422, {"ok": False, "error": "Parámetros serial inválidos"})
            return
        paridad = str(payload.get("paridad") or payload.get("parity") or "none")
        dtr = payload.get("dtr", True)
        rts = payload.get("rts", True)
        if isinstance(dtr, str):
            dtr = dtr.lower() not in ("0", "false", "no")
        if isinstance(rts, str):
            rts = rts.lower() not in ("0", "false", "no")

        try:
            resultado = serial_io.leer_peso(
                puerto,
                baudrate=baudrate,
                bits=bits,
                paridad=paridad,
                paradas=paradas,
                ventana_s=ventana,
                dtr=bool(dtr),
                rts=bool(rts),
            )
        except OSError as exc:
            json_response(self, 422, {"ok": False, "error": str(exc)})
            return
        if not resultado.get("ok"):
            json_response(self, 422, resultado)
            return
        if not guardar:
            json_response(self, 200, resultado)
            return

        registro = {
            "id": secrets.token_hex(8),
            "fecha": mexico_now_iso(),
            "peso": resultado["peso"],
            "unidad": "kg",
            "estable": True,
            "raw": resultado.get("crudo", ""),
            "trama": resultado.get("trama", ""),
            "puerto": resultado.get("puerto", puerto),
            "articulo": _texto(payload, "articulo"),
            "descripcion": _texto(payload, "descripcion", 200),
            "lote": _texto(payload, "lote"),
            "bultos": _texto(payload, "bultos", 20),
            "operador": _texto(payload, "operador"),
            "nota": _texto(payload, "nota", 200),
            "origen": "http",
        }
        lista = load_pesos()
        lista.insert(0, registro)
        save_pesos(lista[:500])
        resultado["registro"] = registro
        json_response(self, 200, resultado)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/") and not autorizado(self):
            json_response(self, 401, {"ok": False, "error": "Token inválido"})
            return

        payload = self._json_body()
        if payload is None:
            json_response(self, 400, {"ok": False, "error": "JSON inválido"})
            return

        if path == "/api/leer-peso":
            self._leer_peso(payload, guardar=False)
            return
        if path == "/api/pesar":
            self._leer_peso(payload, guardar=True)
            return
        if path != "/api/registrar-peso":
            self.send_error(404)
            return

        try:
            peso = float(payload.get("peso"))
        except (TypeError, ValueError):
            json_response(self, 422, {"ok": False, "error": "Peso inválido"})
            return

        if peso != peso or peso in (float("inf"), float("-inf")):
            json_response(self, 422, {"ok": False, "error": "Peso inválido"})
            return

        registro = {
            "id": secrets.token_hex(8),
            "peso": round(peso, 4),
            "unidad": str(payload.get("unidad", "kg")).strip() or "kg",
            "estable": bool(payload.get("estable", True)),
            "raw": str(payload.get("raw", "")).strip(),
            "trama": str(payload.get("trama", "")).strip(),
            "nota": _texto(payload, "nota", 200),
            "articulo": _texto(payload, "articulo"),
            "descripcion": _texto(payload, "descripcion", 200),
            "lote": _texto(payload, "lote"),
            "bultos": _texto(payload, "bultos", 20),
            "operador": _texto(payload, "operador"),
            "origen": "manual",
            "fecha": mexico_now_iso(),
        }
        lista = load_pesos()
        lista.insert(0, registro)
        save_pesos(lista[:500])
        json_response(self, 200, {"ok": True, "registro": registro})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), BasculaHandler)
    print("Báscula PRENLIS — servidor Python")
    print("Abre en Chrome: http://%s:%s" % (HOST, PORT))
    print("Ctrl+C para detener")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")
        server.server_close()


if __name__ == "__main__":
    main()
