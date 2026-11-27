import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Panel de Twinpad en el Quick Access Menu.
 *
 * POR QUÉ ASÍ: refleja la interfaz local del Servidor PC (SPEC-010 W2/W4/W9)
 * sin inventar contrato — todo lo que muestra viene de `GET /` y `GET /qr`
 * del puerto loopback 8430; las acciones son `GET /qr/rotate` y `GET /stop`.
 * El arranque y el autoarranque los resuelve el backend del plugin.
 */

type VideoConfig = { width: number; height: number; bitrate_kbps: number };

type Status = {
  running: boolean;
  port: number;
  uri: string | null;
  ttl_ms: number | null;
  svg: string | null;
  sessions: number | null;
  games: string[];
  video: VideoConfig | null;
  virtual_pad: boolean | null;
  autostart: boolean;
  can_start: boolean;
};

type QrPayload = { ok: boolean; uri?: string; ttl_ms?: number; svg?: string; error?: string };
type ActionResult = { ok: boolean; running?: boolean; enabled?: boolean; error?: string };

const getStatus = callable<[], Status>("get_status");
const regenerateQr = callable<[], QrPayload>("regenerate_qr");
const stopServer = callable<[], ActionResult>("stop_server");
const startServer = callable<[], ActionResult>("start_server");
const setAutostart = callable<[enabled: boolean], ActionResult>("set_autostart");
const setVirtualPad = callable<[enabled: boolean], ActionResult>("set_virtual_pad");

/** Sondeo del panel: 5 s de estado; el contador del QR corre en local. */
const POLL_MS = 5000;

const EMPTY: Status = {
  running: false,
  port: 8430,
  uri: null,
  ttl_ms: null,
  svg: null,
  sessions: null,
  games: [],
  video: null,
  virtual_pad: null,
  autostart: false,
  can_start: false,
};

function svgDataUrl(svg: string): string {
  const bytes = new TextEncoder().encode(svg);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return `data:image/svg+xml;base64,${btoa(binary)}`;
}

/** Copia al portapapeles de Steam; fallback si `navigator.clipboard` falta. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

function mmss(ms: number): string {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function Content() {
  const [status, setStatus] = useState<Status>(EMPTY);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const deadlineRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getStatus();
      setStatus(next);
      if (next.running && typeof next.ttl_ms === "number") {
        const at = Date.now() + next.ttl_ms;
        deadlineRef.current = at;
        setDeadline(at);
        setRemaining(next.ttl_ms);
      } else {
        deadlineRef.current = null;
        setDeadline(null);
        setRemaining(null);
      }
    } catch (error) {
      console.warn("Twinpad: no se pudo leer el estado", error);
      // D6: tras fallo no se reconsulta en cada tick; se pospone la siguiente lectura.
      deadlineRef.current = Date.now() + POLL_MS;
      setDeadline(deadlineRef.current);
      setRemaining(POLL_MS);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const poll = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(poll);
  }, [refresh]);

  // Contador del QR (W9): se recalcula en local, sin sondear el servidor.
  useEffect(() => {
    if (deadline === null) return;
    const tick = setInterval(() => {
      const left = (deadlineRef.current ?? Date.now()) - Date.now();
      setRemaining(Math.max(0, left));
      // Al vencer se reconsulta UNA vez (no rota): el servidor emite QR
      // nuevo si el token de 300 s caducó (W8/W9 automático).
      if (left <= 0) void refresh();
    }, 500);
    return () => clearInterval(tick);
  }, [deadline, refresh]);

  const run = async (label: string, action: () => Promise<ActionResult>) => {
    setBusy(true);
    try {
      const result = await action();
      if (!result.ok) {
        toaster.toast({ title: `Twinpad — ${label}`, body: result.error ?? "Operación fallida." });
      }
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const onRegenerate = async () => {
    setBusy(true);
    try {
      const result = await regenerateQr();
      if (!result.ok || !result.svg || !result.uri) {
        toaster.toast({ title: "Twinpad", body: "No se pudo regenerar el QR; se conserva el vigente." });
        return;
      }
      setStatus((current) => ({
        ...current,
        uri: result.uri ?? current.uri,
        ttl_ms: result.ttl_ms ?? current.ttl_ms,
        svg: result.svg ?? current.svg,
      }));
      const ttl = result.ttl_ms ?? 0;
      deadlineRef.current = Date.now() + ttl;
      setDeadline(deadlineRef.current);
      setRemaining(ttl);
    } catch (error) {
      console.warn("Twinpad: regenerar QR falló", error);
      toaster.toast({ title: "Twinpad", body: "No se pudo regenerar el QR; se conserva el vigente." });
    } finally {
      setBusy(false);
    }
  };

  const onCopy = async () => {
    if (!status.uri) return;
    const ok = await copyText(status.uri);
    toaster.toast({
      title: "Twinpad",
      body: ok ? "URI copiada; pégala en la app." : "No se pudo copiar el URI.",
    });
  };

  const onAutostart = async (enabled: boolean) => {
    setBusy(true);
    try {
      const result = await setAutostart(enabled);
      if (!result.ok) {
        toaster.toast({
          title: "Twinpad",
          body: result.error ?? "No se pudo cambiar el autoarranque.",
        });
      }
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const onVirtualPad = async (enabled: boolean) => {
    setBusy(true);
    try {
      const result = await setVirtualPad(enabled);
      if (!result.ok) {
        toaster.toast({
          title: "Twinpad",
          body: result.error ?? "No se pudo cambiar el mando virtual.",
        });
      }
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PanelSection title="Vinculación">
        <PanelSectionRow>
          <div style={{ color: "rgba(255,255,255,0.6)", fontSize: "12px" }}>
            {status.running ? "Servidor activo" : "Servidor detenido"}
          </div>
        </PanelSectionRow>
        {status.running && status.svg ? (
          <PanelSectionRow>
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                background: "#fff",
                borderRadius: "8px",
                padding: "8px",
              }}
            >
              <img
                src={svgDataUrl(status.svg)}
                alt="QR de vinculación"
                style={{ width: "180px", height: "180px", imageRendering: "pixelated" }}
              />
            </div>
          </PanelSectionRow>
        ) : null}
        {remaining !== null ? (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>
              Se renueva en {mmss(remaining)}
            </div>
          </PanelSectionRow>
        ) : null}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!status.running || busy} onClick={onRegenerate}>
            Regenerar QR
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="URI de vinculación">
        <PanelSectionRow>
          <div
            style={{
              fontFamily: "monospace",
              fontSize: "11px",
              wordBreak: "break-all",
              background: "rgba(0,0,0,0.3)",
              borderRadius: "4px",
              padding: "6px",
              color: status.uri ? "#fff" : "rgba(255,255,255,0.4)",
            }}
          >
            {status.uri ?? "— sin URI mientras el servidor está detenido —"}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!status.uri || busy} onClick={onCopy}>
            Copiar URI
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Estado">
        <PanelSectionRow>
          <div style={{ fontSize: "12px" }}>
            Sesiones activas: {status.sessions ?? "—"}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: "12px" }}>
            Juegos compatibles: {status.games.length > 0 ? status.games.join(", ") : "—"}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: "12px" }}>
            Vídeo:{" "}
            {status.video
              ? `${status.video.width}×${status.video.height} · ${status.video.bitrate_kbps} kbps`
              : "—"}
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Mando virtual">
        <PanelSectionRow>
          <ToggleField
            label="Mando virtual (DualSense)"
            description={
              status.running && status.virtual_pad === null
                ? "No se pudo leer el estado del mando virtual."
                : "Steam lo ve como un DualSense con botón PS, touchpad y giroscopio."
            }
            checked={status.virtual_pad === true}
            disabled={!status.running || status.virtual_pad === null || busy}
            onChange={(enabled: boolean) => void onVirtualPad(enabled)}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Control">
        {status.running ? (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void run("detener", stopServer)}>
              Detener servidor
            </ButtonItem>
          </PanelSectionRow>
        ) : (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || !status.can_start}
              onClick={() => void run("arrancar", startServer)}
            >
              {status.can_start ? "Arrancar servidor" : "Arrancar servidor (no instalado)"}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Inicio">
        <PanelSectionRow>
          <ToggleField
            label="Iniciar servidor al arrancar el equipo"
            description="El servidor se inicia solo al encender el equipo."
            checked={status.autostart}
            disabled={busy}
            onChange={(enabled: boolean) => void onAutostart(enabled)}
          />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

/**
 * Marca del plugin: solo los dos trapecios enfrentados del logo, sin lienzo
 * ni fondo. Se pintan con `currentColor` para que tomen el color del tema,
 * como el resto de iconos de plugins del QAM. El viewBox se recorta al dibujo
 * para que ocupen el icono entero y no queden pequeños.
 */
const TwinpadMark = () => (
  <svg viewBox="256 313 742 572" width="1em" height="1em" aria-hidden="true">
    <g fill="currentColor">
      <path d="M530,340C565,328 601,352 601,392L601,815C601,850 575,870 540,864L312,778C285,768 271,745 271,720L271,478C271,450 288,428 312,420Z" />
      <path d="M724,340C689,328 653,352 653,392L653,815C653,850 679,870 714,864L942,778C969,768 983,745 983,720L983,478C983,450 966,428 942,420Z" />
    </g>
  </svg>
);

export default definePlugin(() => {
  return {
    name: "Twinpad",
    titleView: <div className={staticClasses.Title}>Twinpad</div>,
    content: <Content />,
    icon: <TwinpadMark />,
    onDismount() {
      console.log("Twinpad: panel descargado");
    },
  };
});
