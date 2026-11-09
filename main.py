# Backend del plugin Decky «Twinpad» (SPEC-010 W2/W4/W8/W9, CR-061/CR-073).
#
# POR QUÉ ASÍ: el plugin NO abre un canal nuevo con el Servidor PC. Reutiliza
# la superficie local ya contratada — `GET /`, `GET /qr`, `GET /qr/rotate` y
# `GET /stop` en 127.0.0.1:8430 — y presenta en el QAM de Steam el mismo
# contenido que la página de gestión: QR vigente, URI copiable, estado
# (sesiones/juegos/video) y parada. Único añadido de host: arrancar el
# proceso del servidor cuando no está corriendo (misma responsabilidad de
# host que en Cemu) y conmutar la entrada XDG de autoarranque (W5).
#
# W6 se conserva por construcción: todas las peticiones van a 127.0.0.1 y el
# puerto es configurable solo por variable de entorno del host.

import asyncio
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import decky

# Puerto local de la página de gestión (SPEC-010 §2: 8430 por defecto).
LOCAL_PORT = int(os.environ.get("TWINPAD_LOCAL_PORT", "8430"))
LOCAL_HOST = "127.0.0.1"

# Presupuesto por petición: la página es local y responde en milisegundos;
# un timeout corto mantiene el QAM fluido aunque el servidor esté caído.
HTTP_TIMEOUT_S = 1.5

# Espera máxima tras arrancar el binario hasta que `GET /qr` responda.
START_TIMEOUT_S = 10.0

# Entrada de autoarranque (SPEC-010 W5): MISMO artefacto que instala el
# servidor en `twinpad_server::autostart` — ruta y contenido idénticos, para
# que el toggle del QAM y el del tray no diverjan.
AUTOSTART_APP_ID = "twinpad-server"
def autostart_content(exec_path: str) -> str:
    """Contenido del desktop entry; `Exec` es la ruta real (AppImage incluido)."""
    escaped = exec_path.replace("%", "%%")
    if any(c in exec_path for c in ' "`$\\\t'):
        # Dentro de comillas: escapar \ primero, luego " ` $.
        inner = exec_path.replace("\\", "\\\\")
        for c in ('"', "`", "$"):
            inner = inner.replace(c, "\\" + c)
        escaped = '"' + inner.replace("%", "%%") + '"'
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Twinpad Servidor PC\n"
        "Comment=Servidor Twinpad: acepta sesiones de Pairing en segundo plano "
        "(CR-061, SPEC-010 W5)\n"
        f"Exec={escaped}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "StartupNotify=false\n"
    )

# Extracción del estado de la página (W2). La página es HTML de presentación,
# no un contrato JSON: se anclan las tres etiquetas que TEST-0135 fija y, si
# el render cambia, el campo se degrada a «no disponible» en lugar de romper
# el panel.
RE_SESSIONS = re.compile(r"Sesiones activas:\s*(\d+)")
RE_GAMES = re.compile(r'<ul class="panel__list">(.*?)</ul>', re.DOTALL)
RE_LI = re.compile(r"<li>(.*?)</li>", re.DOTALL)
RE_VIDEO = re.compile(r"Video del Plugin:\s*(\d+)×(\d+)\s*a\s*(\d+)\s*kbps")


class Plugin:
    def __init__(self):
        self._process = None

    # ------------------------------------------------------------------
    # HTTP local (loopback only, W6)
    # ------------------------------------------------------------------
    def _request(self, path: str, action: bool = False) -> str:
        """GET síncrono a la interfaz local; lanza en error de transporte.

        `action=True` envía `X-Twinpad-Action` (S1: `/stop` y `/qr/rotate`
        lo exigen para no aceptar peticiones cross-site).
        """
        url = f"http://{LOCAL_HOST}:{LOCAL_PORT}{path}"
        headers = {"X-Twinpad-Action": "1"} if action else {}
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            return response.read().decode("utf-8", "replace")

    def _is_up(self) -> bool:
        """Sondeo de vida: cualquier respuesta HTTP del puerto local cuenta.

        POR QUÉ no basta el 200: `GET /qr` devuelve 503 cuando el SVG no es
        renderizable (E7) y el servidor sigue vivo; tratar ese 503 como
        «detenido» apagaría el panel con el servidor corriendo.
        """
        try:
            self._request("/qr")
            return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _qr_payload(self, path: str, action: bool = False) -> dict:
        payload = json.loads(self._request(path, action=action))
        return {
            "ok": True,
            "uri": payload.get("uri"),
            "ttl_ms": payload.get("ttl_ms"),
            "svg": payload.get("svg"),
        }

    def _page_state(self) -> dict:
        """Estado W2 desde `GET /`: sesiones, juegos y video vigentes."""
        state = {
            "sessions": None,
            "games": [],
            "video": None,
        }
        try:
            html = self._request("/")
        except (urllib.error.URLError, OSError):
            return state
        sessions = RE_SESSIONS.search(html)
        if sessions:
            state["sessions"] = int(sessions.group(1))
        games_block = RE_GAMES.search(html)
        if games_block:
            games = [item.strip() for item in RE_LI.findall(games_block.group(1))]
            state["games"] = [game for game in games if game and not game.startswith("—")]
        video = RE_VIDEO.search(html)
        if video:
            state["video"] = {
                "width": int(video.group(1)),
                "height": int(video.group(2)),
                "bitrate_kbps": int(video.group(3)),
            }
        return state

    # ------------------------------------------------------------------
    # Autoarranque (W5, mismo desktop entry que el servidor)
    # ------------------------------------------------------------------
    @staticmethod
    def _autostart_path() -> Path:
        base = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if base:
            return Path(base) / "autostart" / f"{AUTOSTART_APP_ID}.desktop"
        return Path(os.path.expanduser("~")) / ".config" / "autostart" / f"{AUTOSTART_APP_ID}.desktop"

    def _autostart_on(self) -> bool:
        return self._autostart_path().exists()

    # ------------------------------------------------------------------
    # Binario del servidor (para arrancarlo desde el QAM)
    # ------------------------------------------------------------------
    @staticmethod
    def _server_binary() -> str | None:
        override = os.environ.get("TWINPAD_SERVER_BIN", "").strip()
        candidates = [
            override,
            shutil.which("twinpad-server") or "",
            str(Path(os.path.expanduser("~")) / "twinpad" / "target" / "release" / "twinpad-server"),
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    # ------------------------------------------------------------------
    # Superficie expuesta al frontend
    # ------------------------------------------------------------------
    async def get_status(self) -> dict:
        """Estado completo del panel: vida, QR, estado W2 y autoarranque."""
        running = await asyncio.to_thread(self._is_up)
        status = {
            "running": running,
            "port": LOCAL_PORT,
            "uri": None,
            "ttl_ms": None,
            "svg": None,
            "sessions": None,
            "games": [],
            "video": None,
            "autostart": self._autostart_on(),
            "can_start": self._server_binary() is not None,
        }
        if not running:
            return status
        status.update(await asyncio.to_thread(self._page_state))
        try:
            qr = await asyncio.to_thread(self._qr_payload, "/qr")
        except (urllib.error.URLError, OSError, ValueError) as error:
            # W9/E7: sin respuesta válida se conserva el QR vigente; aquí no
            # hay vigente todavía, así que el panel deja los campos vacíos.
            decky.logger.warning(f"Twinpad: /qr sin respuesta válida ({error})")
            return status
        status["uri"] = qr.get("uri")
        status["ttl_ms"] = qr.get("ttl_ms")
        status["svg"] = qr.get("svg")
        return status

    async def regenerate_qr(self) -> dict:
        """«Regenerar QR»: ROTA el token (W9 manual, `/qr/rotate`)."""
        try:
            return await asyncio.to_thread(self._qr_payload, "/qr/rotate", True)
        except (urllib.error.URLError, OSError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    async def stop_server(self) -> dict:
        """Parada con gracia (W4) por `GET /stop`; luego confirma el cierre."""
        try:
            await asyncio.to_thread(self._request, "/stop", True)
        except (urllib.error.URLError, OSError) as error:
            # El server pudo caer antes de responder: se comprueba igual.
            decky.logger.info(f"Twinpad: /stop sin respuesta ({error})")
        for _ in range(20):
            if not await asyncio.to_thread(self._is_up):
                return {"ok": True, "running": False}
            await asyncio.sleep(0.25)
        return {"ok": False, "running": True, "error": "El servidor sigue en ejecución."}

    async def start_server(self) -> dict:
        """Arranca el binario del servidor si el puerto local no responde."""
        if await asyncio.to_thread(self._is_up):
            return {"ok": True, "running": True}
        binary = self._server_binary()
        if binary is None:
            return {
                "ok": False,
                "running": False,
                "error": "No se encontró el binario twinpad-server (define TWINPAD_SERVER_BIN).",
            }
        try:
            self._process = subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            return {"ok": False, "running": False, "error": f"No se pudo arrancar: {error}"}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_TIMEOUT_S
        while loop.time() < deadline:
            if await asyncio.to_thread(self._is_up):
                return {"ok": True, "running": True}
            if self._process.poll() is not None:
                return {"ok": False, "running": False, "error": "El servidor terminó al arrancar."}
            await asyncio.sleep(0.25)
        return {"ok": False, "running": False, "error": "El servidor no respondió a tiempo."}

    async def set_autostart(self, enabled: bool) -> dict:
        """W5: instala/elimina el desktop entry (idempotente, E5 con error)."""
        path = self._autostart_path()

        def write() -> None:
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                exec_path = os.environ.get("APPIMAGE", "").strip() or (
                    Plugin._server_binary() or AUTOSTART_APP_ID
                )
                path.write_text(autostart_content(exec_path), encoding="utf-8")
            elif path.exists():
                path.unlink()

        try:
            await asyncio.to_thread(write)
        except OSError as error:
            return {"ok": False, "enabled": self._autostart_on(), "error": str(error)}
        return {"ok": True, "enabled": self._autostart_on()}

    # ------------------------------------------------------------------
    # Ciclo de vida del plugin
    # ------------------------------------------------------------------
    async def _main(self):
        decky.logger.info(
            f"Twinpad: panel listo (interfaz local en http://{LOCAL_HOST}:{LOCAL_PORT}/)."
        )

    async def _unload(self):
        # El servidor es del usuario: el plugin no lo detiene al descargarse.
        decky.logger.info("Twinpad: panel descargado.")

    async def _uninstall(self):
        decky.logger.info("Twinpad: plugin desinstalado.")
