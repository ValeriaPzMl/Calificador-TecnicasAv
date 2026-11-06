// Mis exámenes: lista con bajar PDF, calificar, ver resultados, editar y borrar.
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { apiJson, downloadFile } from "../lib/api.js";
import { log } from "../lib/log.js";

export const PAGE_KEY = "calificador.page";

export function savedPage() {
  try { return localStorage.getItem(PAGE_KEY) || "letter"; } catch { return "letter"; }
}

export default function Exams() {
  const navigate = useNavigate();
  const location = useLocation();
  const [exams, setExams] = useState(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(savedPage);
  const [busyId, setBusyId] = useState(null);
  const notice = location.state?.notice;
  const highlight = location.state?.examId;

  async function load() {
    setError("");
    try {
      const data = await apiJson("/api/exams");
      log.info(`${data.length} exámenes`);
      setExams(data.slice().reverse()); // los más nuevos primero
    } catch (err) {
      if (err.status !== 401) setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  function choosePage(value) {
    setPage(value);
    try { localStorage.setItem(PAGE_KEY, value); } catch { /* sin almacenamiento */ }
  }

  async function downloadPdf(exam) {
    setBusyId(exam.id);
    setError("");
    try {
      await downloadFile(`/api/exams/${encodeURIComponent(exam.id)}/sheet.pdf?page=${page}`,
                         `${exam.id}.pdf`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  async function remove(exam) {
    const lost = exam.results
      ? `\n\nSe borrarán también sus ${exam.results} resultado(s) calificado(s). No se puede deshacer.`
      : "\n\nNo se puede deshacer.";
    if (!window.confirm(`¿Borrar "${exam.title}"?${lost}`)) return;
    setBusyId(exam.id);
    try {
      const r = await apiJson(`/api/exams/${encodeURIComponent(exam.id)}`, { method: "DELETE" });
      log.info("examen borrado", exam.id, r);
      setExams((list) => list.filter((e) => e.id !== exam.id));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="row between">
        <h1>Mis exámenes</h1>
        <Link className="btn small" to="/examenes/nuevo">+ Nuevo examen</Link>
      </div>
      {notice && <div className="banner">{notice}</div>}
      {error && <p className="error">{error}</p>}

      {exams === null && !error && <div className="card center"><div className="spinner" /></div>}

      {exams?.length === 0 && (
        <div className="card">
          <h2>Todavía no tienes exámenes</h2>
          <p>Crea uno, descarga su hoja de respuestas en PDF, imprímela y califícala con la cámara.</p>
          <Link className="btn" to="/examenes/nuevo">Crear mi primer examen</Link>
        </div>
      )}

      {exams?.length > 0 && (
        <div className="row note" style={{ marginBottom: 8 }}>
          <span>Tamaño de hoja para el PDF:</span>
          <select value={page} onChange={(e) => choosePage(e.target.value)} style={{ width: "auto", margin: 0 }}>
            <option value="letter">Carta</option>
            <option value="a4">A4</option>
          </select>
        </div>
      )}

      {exams?.map((exam) => (
        <div key={exam.id} className="card exam-card"
             style={exam.id === highlight ? { borderColor: "var(--accent)" } : undefined}>
          <h2>{exam.title}</h2>
          <div className="note">
            ID {exam.id} · {exam.questions} preguntas · {exam.results} resultado(s)
          </div>
          <div className="exam-actions">
            <button onClick={() => downloadPdf(exam)} disabled={busyId === exam.id}>
              {busyId === exam.id ? "…" : "Descargar PDF"}
            </button>
            <button className="secondary" onClick={() => navigate(`/calificar?examen=${encodeURIComponent(exam.id)}`)}>
              Calificar
            </button>
            <button className="secondary" onClick={() => navigate(`/examenes/${encodeURIComponent(exam.id)}/resultados`)}>
              Resultados ({exam.results})
            </button>
            <button className="secondary" onClick={() => navigate(`/examenes/${encodeURIComponent(exam.id)}`)}>
              Editar
            </button>
            <button className="danger" onClick={() => remove(exam)} disabled={busyId === exam.id}>
              Borrar
            </button>
          </div>
        </div>
      ))}
    </>
  );
}
