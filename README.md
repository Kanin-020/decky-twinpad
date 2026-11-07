# Twinpad — plugin de Decky Loader

Panel de Twinpad en el Quick Access Menu de Steam (Steam Deck / SteamOS).
Muestra **lo mismo que la interfaz local del Servidor PC** (SPEC-010) y
permite operarla sin salir de Game Mode:

- QR de vinculación vigente, con contador `mm:ss` hasta que se renueva.
- «Regenerar QR»: rota el token al instante (`GET /qr/rotate`, W9 manual).
- URI de vinculación copiable con un botón.
- Estado: sesiones activas, juegos compatibles y video configurado del
  Plugin (W2).
- «Detener servidor» (parada con gracia, W4) y «Arrancar servidor».
- «Iniciar servidor al arrancar el equipo» (autoarranque, W5).

## Por qué no hay canal nuevo

El plugin **no** define protocolo propio: consume la superficie local ya
contratada en `127.0.0.1:8430` — `GET /`, `GET /qr`, `GET /qr/rotate` y
`GET /stop` (SPEC-010 §4–§5, W6 loopback). El backend solo añade lo que es
responsabilidad del host: lanzar el proceso del servidor cuando no está
corriendo y conmutar la misma entrada XDG de autoarranque que instala
`server/src/autostart.rs` (contenido verificado idéntico en el harness de
verificación).

El servidor debe correr **en el mismo dispositivo** que Decky: la página de
gestión es loopback-only y no se sirve a la LAN.

## Instalación

```bash
pnpm install
pnpm run build          # genera dist/index.js
```

Con Decky en modo desarrollador:

```bash
# copia el plugin (con dist/) a la carpeta de plugins del usuario
cp -r . ~/homebrew/plugins/twinpad
```

y recarga Decky (`Ajustes → Decky → Recargar`). El plugin aparece como
«Twinpad» en el QAM.

## Variables de entorno (host)

| Variable | Uso |
|---|---|
| `TWINPAD_LOCAL_PORT` | Puerto de la interfaz local (por defecto 8430, el de `webui::PORT`). |
| `TWINPAD_SERVER_BIN` | Ruta explícita del binario `twinpad-server` para «Arrancar servidor». Sin ella se busca en `PATH` y en `~/twinpad/target/release/`. |

## Límites conocidos

- **Autoarranque en Game Mode:** la entrada XDG `autostart/twinpad-server.desktop`
  es la que define W5 y funciona en Desktop Mode; en Game Mode SteamOS no
  ejecuta autoarranque XDG. Si se necesita arranque en Game Mode, hace falta
  una decisión de producto (unidad systemd de usuario o integración en el
  arranque de Decky) — hoy no se implementa.
- **Estado desde HTML:** sesiones, juegos y video solo existen en `GET /`
  (no hay endpoint JSON). El backend ancla las etiquetas que fija TEST-0135
  y, si el render cambia, muestra «—» en lugar de romper el panel. Un
  `GET /status` JSON sería la solución limpia, pero es contrato nuevo
  (requiere CR/SPEC).
- **Sin audio/cámara/QR de cámara** en el panel: no forman parte de la
  superficie local del servidor.
- **Trazabilidad:** el plugin no tiene REQ/SPEC/CR ni TEST ID asignado; el
  harness de verificación vive fuera del repo (`tests/` del proyecto no
  aplica a un plugin TypeScript/Python). Requiere decisión del owner.

## Créditos

Deriva del `decky-plugin-template` (BSD-3-Clause, ver `LICENSE`).
