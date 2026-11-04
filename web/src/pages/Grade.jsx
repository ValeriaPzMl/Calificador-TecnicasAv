// Calificar: cámara en vivo, tomar foto (cámara nativa, mejor calidad) o
// subirla de la galería. Es la misma lógica de la página de prueba
// (api/static/index.html), pasada a React:
// - En vivo: getUserMedia → un cuadro a la vez a /api/scan/sessions/{id}/frame;
//   el servidor responde estado, esquinas y progreso; en "lista" trae la nota.
// - Foto: /api/grade.
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import ResultCard from "../components/ResultCard.jsx";
import { CAMERA_CONFIG, STATE_COLORS } from "../config/camera.js";
import { ApiError, apiJson, jsonBody } from "../lib/api.js";
import { log } from "../lib/log.js";
import { savedPage } from "./Exams.jsx";

const liveSupported = typeof window !== "undefined" && window.isSecureContext
  && Boolean(navigator.mediaDevices?.getUserMedia);

export default function Grade() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [exams, setExams] = useState([]);
  const [opts, setOpts] = useState({ exam_id: params.get("examen") || "", page: savedPage(), dry_run: false });
  const [screen, setScreen] = useState("home"); // home | busy | live | result | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const lastMode = useRef(null); // "live" | "camera" | "gallery"
  const cameraInput = useRef(null);
  const galleryInput = useRef(null);

  useEffect(() => {
    apiJson("/api/exams").then(setExams).catch((err) => log.warn("exámenes:", err.message));
  }, []);

  function fail(msg) { setError(msg); setScreen("error"); }
  function done(r) { setResult(r); setScreen("result"); window.scrollTo(0, 0); }

  async function gradePhoto(file) {
    setScreen("busy");
    const form = new FormData();
    form.append("photo", file);
    form.append("exam_id", opts.exam_id);
    form.append("page", opts.page);
    form.append("dry_run", opts.dry_run);
    log.info("calificando foto", file.name, `${Math.round(file.size / 1024)} KB`);
    try {
      const data = await apiJson("/api/grade", { method: "POST", body: form });
      done(data.result);
    } catch (err) {
      if (err.status !== 401) fail(err instanceof ApiError ? err.message : "No hay conexión con el servidor.");
    }
  }

  function onFile(ev, mode) {
    const file = ev.target.files[0];
    ev.target.value = ""; // para poder elegir la misma foto otra vez
    if (file) { lastMode.current = mode; gradePhoto(file); }
  }

  function again() {
    if (lastMode.current === "live") return setScreen("live");
    (lastMode.current === "camera" ? cameraInput : galleryInput).current.click();
  }

  if (screen === "live")
    return <LiveScan options={opts} onDone={done} onError={fail} onCancel={() => setScreen("home")} />;

  if (screen === "busy")
    return <div className="card center"><div className="spinner" /><p>Calificando…</p></div>;

  return (
    <>
      <input ref={cameraInput} className="hidden" type="file" accept="image/*" capture="environment"
             onChange={(e) => onFile(e, "camera")} />
      <input ref={galleryInput} className="hidden" type="file" accept="image/*"
             onChange={(e) => onFile(e, "gallery")} />

      {screen === "result" && result && (
        <ResultCard result={result} onAgain={again} onExit={() => setScreen("home")} />
      )}

      {screen === "error" && (
        <div className="card">
          <h1>No se pudo calificar</h1>
          <p className="error">{error}</p>
          <button onClick={again}>Intentar de nuevo</button>
          <button className="secondary" onClick={() => setScreen("home")}>Inicio</button>
        </div>
      )}

      {screen === "home" && (
        <>
          <h1>Calificar</h1>
          {exams.length === 0 && (
            <div className="banner">
              Primero crea un examen y descarga su hoja.{" "}
              <a href="/examenes/nuevo" onClick={(e) => { e.preventDefault(); navigate("/examenes/nuevo"); }}>Crear examen</a>
            </div>
          )}
          <div className="card">
            <button disabled={!liveSupported} onClick={() => { lastMode.current = "live"; setScreen("live"); }}>
              Calificar con la cámara en vivo
            </button>
            {!liveSupported && (
              <p className="note warn">La cámara en vivo necesita HTTPS. Puedes usar "Tomar foto".</p>
            )}
            <button className="secondary" onClick={() => cameraInput.current.click()}>Tomar foto</button>
            <button className="secondary" onClick={() => galleryInput.current.click()}>Subir foto de la galería</button>
            <p className="note">"Tomar foto" usa la cámara normal del celular: da la mejor calidad.</p>
          </div>
          <div className="card">
            <label htmlFor="opt-exam">Examen (si el QR no se puede leer)</label>
            <select id="opt-exam" value={opts.exam_id} onChange={(e) => setOpts({ ...opts, exam_id: e.target.value })}>
              <option value="">Detectar por el QR</option>
              {exams.map((e) => <option key={e.id} value={e.id}>{e.title} ({e.id})</option>)}
            </select>
            <label htmlFor="opt-page">Tamaño de hoja (solo sin QR)</label>
            <select id="opt-page" value={opts.page} onChange={(e) => setOpts({ ...opts, page: e.target.value })}>
              <option value="letter">Carta</option>
              <option value="a4">A4</option>
            </select>
            <label className="check">
              <input type="checkbox" checked={opts.dry_run} onChange={(e) => setOpts({ ...opts, dry_run: e.target.checked })} />
              Solo probar (no guardar)
            </label>
          </div>
        </>
      )}
    </>
  );
}

// Pantalla completa con la cámara. Se monta al empezar y se desmonta al
// terminar; al desmontarse apaga la cámara y cancela el escaneo.
function LiveScan({ options, onDone, onError, onCancel }) {
  const videoRef = useRef(null);
  const viewRef = useRef(null);
  const statusRef = useRef(null);
  const [status, setStatus] = useState(null);
  const [cameras, setCameras] = useState([]);
  const [deviceId, setDeviceId] = useState(null); // la que eligió el usuario
  const [currentId, setCurrentId] = useState(null); // la que abrió el sistema la primera vez
  const [opening, setOpening] = useState(true);

  useEffect(() => {
    let stopped = false;
    let stream = null;
    let scanId = null;
    let wakeLock = null;
    let raf = 0;

    function stop() {
      if (stopped) return;
      stopped = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      wakeLock?.release().catch(() => {});
      if (scanId) apiJson(`/api/scan/sessions/${scanId}`, { method: "DELETE" }).catch(() => {});
      log.info("cámara cerrada");
    }

    async function openCamera() {
      const video = { width: { ideal: CAMERA_CONFIG.width }, height: { ideal: CAMERA_CONFIG.height } };
      if (deviceId) video.deviceId = { exact: deviceId };
      else video.facingMode = { ideal: CAMERA_CONFIG.facingMode };
      const s = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      const track = s.getVideoTracks()[0];
      if (CAMERA_CONFIG.continuousFocus && track.getCapabilities?.().focusMode?.includes("continuous")) {
        try { await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch { /* no soportado */ }
      }
      const set = track.getSettings();
      log.info(`cámara: ${track.label} ${set.width}x${set.height}`);
      return s;
    }

    // Dibuja el video con el contorno de la hoja según el último estado.
    function render() {
      if (stopped) return;
      const video = videoRef.current, view = viewRef.current;
      if (video?.videoWidth && view) {
        if (view.width !== video.videoWidth) { view.width = video.videoWidth; view.height = video.videoHeight; }
        const ctx = view.getContext("2d");
        ctx.drawImage(video, 0, 0);
        const st = statusRef.current;
        if (st) {
          const sx = view.width / st.frame_size[0], sy = view.height / st.frame_size[1];
          const lw = Math.max(3, view.width / 300);
          ctx.strokeStyle = ctx.fillStyle = STATE_COLORS[st.state] || "#fff";
          const pts = st.corner_order.filter((k) => st.corners[k])
            .map((k) => [st.corners[k][0] * sx, st.corners[k][1] * sy]);
          if (pts.length === 4) {
            ctx.beginPath();
            pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
            ctx.closePath();
            ctx.lineWidth = lw * 2;
            ctx.stroke();
          }
          for (const [x, y] of pts) { ctx.beginPath(); ctx.arc(x, y, lw * 4, 0, 2 * Math.PI); ctx.fill(); }
        }
      }
      raf = requestAnimationFrame(render);
    }

    // Un cuadro a la vez: se manda el siguiente cuando llega la respuesta del anterior.
    async function sendLoop() {
      const video = videoRef.current;
      const grab = document.createElement("canvas");
      while (!stopped) {
        const scale = Math.min(1, CAMERA_CONFIG.sendMaxSide / Math.max(video.videoWidth, video.videoHeight));
        grab.width = Math.round(video.videoWidth * scale);
        grab.height = Math.round(video.videoHeight * scale);
        grab.getContext("2d").drawImage(video, 0, 0, grab.width, grab.height);
        const blob = await new Promise((r) => grab.toBlob(r, "image/jpeg", CAMERA_CONFIG.jpegQuality));
        const form = new FormData();
        form.append("frame", blob, "frame.jpg");
        const st = await apiJson(`/api/scan/sessions/${scanId}/frame`, { method: "POST", body: form });
        if (stopped) return;
        statusRef.current = st;
        setStatus(st);
        if (st.state === "lista") {
          scanId = null; // ya terminó: no hace falta cancelarlo
          stop();
          return onDone(st.result);
        }
      }
    }

    (async () => {
      try {
        stream = await openCamera();
        if (stopped) return stream.getTracks().forEach((t) => t.stop());
        const video = videoRef.current;
        video.srcObject = stream;
        await video.play();
        setOpening(false);
        const devices = await navigator.mediaDevices.enumerateDevices();
        setCameras(devices.filter((d) => d.kind === "videoinput"));
        if (!deviceId) setCurrentId(stream.getVideoTracks()[0].getSettings().deviceId);
        try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* sin wake lock */ }
        scanId = (await apiJson("/api/scan/sessions", jsonBody("POST", options))).scan_id;
        log.info("escaneo en vivo", scanId);
        raf = requestAnimationFrame(render);
        await sendLoop();
      } catch (err) {
        if (stopped) return;
        stop();
        if (err.status === 401) return;
        if (err instanceof ApiError) return onError(err.message);
        if (err instanceof DOMException) // permiso negado, cámara ocupada…
          return onError(`No se pudo abrir la cámara: ${err.message}. Revisa el permiso o usa "Tomar foto".`);
        onError("Se perdió la conexión con el servidor.");
      }
    })();

    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId]);

  return (
    <div className="live">
      <div className="live-bar">
        <div className="live-msg" style={{ color: status ? STATE_COLORS[status.state] : "#fff" }}>
          {opening ? "Abriendo la cámara…" : status?.message ?? "Buscando la hoja…"}
        </div>
        <div className="live-sub">
          <span>Examen: {status?.exam_id || "—"}</span>
          {status && (
            <span className="progress">
              {Array.from({ length: status.needed }, (_, i) => <span key={i} className={i < status.progress ? "on" : ""} />)}
            </span>
          )}
        </div>
      </div>
      <canvas ref={viewRef} />
      <video ref={videoRef} className="raw" playsInline muted />
      <div className="live-actions">
        {cameras.length > 1 ? (
          <select aria-label="Cámara" value={deviceId ?? currentId ?? ""} onChange={(e) => setDeviceId(e.target.value)}>
            {cameras.map((c, i) => <option key={c.deviceId} value={c.deviceId}>{c.label || `Cámara ${i + 1}`}</option>)}
          </select>
        ) : <span style={{ flex: 1 }} />}
        <button className="secondary" onClick={onCancel}>Salir</button>
      </div>
    </div>
  );
}
