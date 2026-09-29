"""Lectura puntual del puerto serie. Solo biblioteca estándar (termios en Linux)."""

from __future__ import annotations

import errno
import fcntl
import os
import re
import select
import struct
import termios
import threading
import time
from pathlib import Path
from typing import Any

PUERTO_RE = re.compile(r"^/dev/tty(?:USB|ACM)\d+$")
PESO_RE = re.compile(r"([+-]?\s*\d+(?:[.,]\d+)?)")
TRAMA_COMPLETA = re.compile(r"[+-]?\d+\.\d{2,}")
BAUDS = {
    1200: termios.B1200,
    2400: termios.B2400,
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
}
TIOCMGET = 0x5415
TIOCMSET = 0x5418
TIOCM_DTR = 0x002
TIOCM_RTS = 0x004

_lock = threading.Lock()


def extraer_peso(crudo: str) -> dict[str, Any] | None:
    """Invierte la trama PRENLIS y toma la lectura completa más reciente.

    El indicador manda 8 caracteres al revés. "=" es el inicio tras invertir.
    "48.4000=" → "0004.84" → 4.84 kg.
    Se descarta el primer fragmento: la captura casi siempre empieza a media trama.
    """
    if not crudo or "=" not in crudo:
        return None
    partes = crudo[::-1].split("=")
    for parte in partes[1:]:
        if not parte.strip():
            continue
        match = PESO_RE.search(parte)
        if not match:
            continue
        texto = match.group(1).replace(",", ".").replace(" ", "")
        try:
            peso = float(texto)
        except ValueError:
            continue
        if peso != peso or peso in (float("inf"), float("-inf")):
            return None
        trama = parte.strip()
        if not TRAMA_COMPLETA.fullmatch(trama.replace(" ", "").replace(",", ".")):
            continue
        return {"peso": round(peso, 4), "trama": trama}
    return None


def _entrada_puerto(ruta: Path) -> dict[str, Any]:
    return {
        "path": str(ruta),
        "writable": os.access(ruta, os.W_OK),
        "readable": os.access(ruta, os.R_OK),
    }


def listar_puertos() -> list[dict[str, Any]]:
    dev = Path("/dev")
    encontrados: list[dict[str, Any]] = []
    if not dev.is_dir():
        return encontrados
    enlace = dev / "bascula"
    if enlace.exists():
        encontrados.append(_entrada_puerto(enlace))
    for patron in ("ttyUSB*", "ttyACM*"):
        for ruta in sorted(dev.glob(patron)):
            encontrados.append(_entrada_puerto(ruta))
    return encontrados


def puerto_valido(puerto: str) -> bool:
    if puerto == "/dev/bascula":
        try:
            real = os.path.realpath(puerto)
        except OSError:
            return False
        return bool(PUERTO_RE.match(real))
    return bool(PUERTO_RE.match(puerto))


def _configurar(fd: int, baudrate: int, bits: int, paridad: str, paradas: int) -> None:
    iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = termios.tcgetattr(fd)
    del iflag, oflag, lflag
    cflag = termios.CREAD | termios.CLOCAL
    cflag |= termios.CS7 if bits == 7 else termios.CS8
    if paradas == 2:
        cflag |= termios.CSTOPB
    if paridad == "even":
        cflag |= termios.PARENB
    elif paridad == "odd":
        cflag |= termios.PARENB | termios.PARODD
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    velocidad = BAUDS[baudrate]
    termios.tcsetattr(
        fd,
        termios.TCSANOW,
        [0, 0, cflag, 0, velocidad, velocidad, cc],
    )
    termios.tcflush(fd, termios.TCIFLUSH)


def _senales(fd: int, dtr: bool, rts: bool) -> None:
    raw = fcntl.ioctl(fd, TIOCMGET, struct.pack("I", 0))
    flags = struct.unpack("I", raw)[0]
    if dtr:
        flags |= TIOCM_DTR
    else:
        flags &= ~TIOCM_DTR
    if rts:
        flags |= TIOCM_RTS
    else:
        flags &= ~TIOCM_RTS
    fcntl.ioctl(fd, TIOCMSET, struct.pack("I", flags))


def leer_peso(
    puerto: str,
    baudrate: int = 9600,
    bits: int = 8,
    paridad: str = "none",
    paradas: int = 1,
    ventana_s: float = 0.8,
    dtr: bool = True,
    rts: bool = True,
) -> dict[str, Any]:
    """Abre el puerto, escucha una ventana corta y lo cierra. No deja la línea ocupada."""
    if not puerto_valido(puerto):
        return {"ok": False, "error": "Puerto no permitido. Usa /dev/bascula, /dev/ttyUSB0 o /dev/ttyACM0."}
    if baudrate not in BAUDS:
        return {"ok": False, "error": "Baudrate no soportado."}
    if bits not in (7, 8) or paradas not in (1, 2) or paridad not in ("none", "even", "odd"):
        return {"ok": False, "error": "Parámetros serial inválidos."}
    ventana_s = min(2.0, max(0.2, float(ventana_s)))

    if not _lock.acquire(blocking=False):
        return {
            "ok": False,
            "error": "Ya hay una lectura en curso. Espera a que termine la petición anterior.",
        }

    fd = -1
    try:
        try:
            fd = os.open(puerto, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            return _error_apertura(puerto, exc)

        _configurar(fd, baudrate, bits, paridad, paradas)
        try:
            _senales(fd, dtr, rts)
        except OSError:
            pass
        termios.tcflush(fd, termios.TCIFLUSH)

        crudo = ""
        lectura = None
        limite = time.monotonic() + ventana_s
        while time.monotonic() < limite:
            listo, _, _ = select.select([fd], [], [], 0.02)
            if listo:
                try:
                    bloque = os.read(fd, 256)
                except BlockingIOError:
                    bloque = b""
                if bloque:
                    crudo += bloque.decode("latin1", errors="replace")
            if crudo.count("=") >= 2:
                lectura = extraer_peso(crudo)
                if lectura is not None:
                    break

        if lectura is None:
            lectura = extraer_peso(crudo)
        if lectura is None:
            if not crudo:
                return {
                    "ok": False,
                    "error": "La báscula no envió datos. Revisa cable, encendido y modo continuo.",
                    "crudo": "",
                }
            return {
                "ok": False,
                "error": "Llegaron datos pero no forman una trama completa.",
                "crudo": crudo[-64:],
            }
        return {
            "ok": True,
            "peso": lectura["peso"],
            "unidad": "kg",
            "trama": lectura["trama"],
            "crudo": crudo[-64:],
            "puerto": puerto,
        }
    finally:
        if fd >= 0:
            os.close(fd)
        _lock.release()


def _error_apertura(puerto: str, exc: OSError) -> dict[str, Any]:
    if exc.errno in (errno.EACCES, errno.EPERM):
        return {
            "ok": False,
            "error": "Sin permiso sobre %s. Agrega el usuario al grupo dialout y vuelve a entrar a la sesión."
            % puerto,
        }
    if exc.errno == errno.EBUSY:
        return {
            "ok": False,
            "error": "%s está ocupado. Cierra el diagnóstico de Chrome (Desconectar) u otro programa que use el puerto."
            % puerto,
        }
    if exc.errno == errno.ENOENT:
        return {"ok": False, "error": "No existe %s. Reconecta el adaptador USB." % puerto}
    return {"ok": False, "error": "No se pudo abrir %s: %s" % (puerto, exc.strerror)}
