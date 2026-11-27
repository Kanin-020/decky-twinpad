"""TEST-0231 — CR-132: el panel Decky opera el mando virtual del Servidor.

Sin un Decky real, se prueba la capa que sí es determinista: el parseo del
estado de `GET /` y la petición `POST /virtual-pad` con cabecera de acción.
"""

import asyncio
import io
import logging
import sys
import types
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# `main.py` importa decky para logs; el runtime real no existe en el host de tests.
if "decky" not in sys.modules:
    decky = types.ModuleType("decky")
    decky.logger = logging.getLogger("decky-test")
    sys.modules["decky"] = decky

import main  # noqa: E402


PAGE_WITH_PAD = """
<p>Sesiones activas: 2</p>
<ul class="panel__list"><li>Juego A</li><li>Juego B</li></ul>
<p>Video del Plugin: 1280×720 a 15000 kbps</p>
<p>Mando virtual (DualSense): <strong id="pad-state">Activado</strong></p>
"""


class VirtualPadPanelTest(unittest.TestCase):
    def test_page_state_reads_virtual_pad_status(self):
        plugin = main.Plugin()
        plugin._request = lambda path, action=False, data=None: PAGE_WITH_PAD
        state = plugin._page_state()
        self.assertIs(state["virtual_pad"], True)
        self.assertEqual(2, state["sessions"])
        self.assertEqual(["Juego A", "Juego B"], state["games"])

    def test_set_virtual_pad_posts_enabled_with_action_header(self):
        plugin = main.Plugin()
        calls = []

        def fake_request(path, action=False, data=None):
            calls.append((path, action, data))
            return ""

        plugin._request = fake_request
        self.assertEqual(
            {"ok": True, "enabled": True},
            asyncio.run(plugin.set_virtual_pad(True)),
        )
        self.assertEqual(
            {"ok": True, "enabled": False},
            asyncio.run(plugin.set_virtual_pad(False)),
        )
        self.assertEqual(
            [
                ("/virtual-pad", True, {"enabled": "1"}),
                ("/virtual-pad", True, {"enabled": "0"}),
            ],
            calls,
        )

    def test_set_virtual_pad_surfaces_server_error(self):
        plugin = main.Plugin()

        def fail_request(path, action=False, data=None):
            raise urllib.error.HTTPError(
                "http://127.0.0.1/virtual-pad",
                500,
                "Internal Server Error",
                None,
                io.BytesIO(b"/dev/uhid no disponible"),
            )

        plugin._request = fail_request
        result = asyncio.run(plugin.set_virtual_pad(True))
        self.assertIs(result["ok"], False)
        self.assertEqual("/dev/uhid no disponible", result["error"])


if __name__ == "__main__":
    unittest.main()
