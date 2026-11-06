// Resultados de un examen: resumen, exportar, hojas por revisar y elegir
// cuál cuenta cuando una matrícula se escaneó varias veces.
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import ReviewImage from "../components/ReviewImage.jsx";
import { apiJson, downloadFile } from "../lib/api.js";
import { log } from "../lib/log.js";
import {
  STATUS_TEXT, filterResults, fmtDate, fmtScore, pendingGroups, resultStatus, summary,
} from "../lib/results.js";

const FILTERS = [["all", "Todas"], ["review", "Por revisar"], ["duplicates", "Duplicadas"]];

export default function Results() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const filter = params.get("filtro") || "all";
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const examUrl = `/api/exams/${encodeURIComponent(id)}`;

  async function load() {
    setError("");
    try {
      const d = await apiJson(`${examUrl}/results`);
      log.info(`${d.results.length} hojas de ${id}`);
      setData(d);
    } catch (err) {
      if (err.status !== 401) setError(err.message);
    }
  }

  useEffect(() => { load(); }, [id]);

  const rows = data?.results ?? [];
  const stats = useMemo(() => summary(rows), [rows]);
  const pending = useMemo(() => pendingGroups(rows), [rows]);
  const shown = useMemo(() => filterResults(rows, filter), [rows, filter]);

  async function exportAs(fmt) {
    setBusy(fmt);
    setError("");
    try {
      await downloadFile(`${examUrl}/results.${fmt}`, `${id}_resultados.${fmt}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function choose(row) {
    setBusy(row.id);
    try {
      await apiJson(`/api/results/${row.id}/choose`, { method: "POST" });
      log.info("hoja elegida", row.id, "para", row.student_id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  if (!data) {
    return error
      ? <><BackLink /><p className="error">{error}</p></>
      : <div className="card center"><div className="spinner" /></div>;
  }

  return (
    <>
      <BackLink />
      <h1>{data.exam.name}</h1>
      {error && <p className="error">{error}</p>}

      <div className="card">
        <div className="stats">
          <Stat label="Hojas" value={stats.sheets} />
          <Stat label="Alumnos con nota" value={stats.counted} />
          <Stat label="Promedio" value={stats.average == null ? "—"
            : `${fmtScore(stats.average)} / ${fmtScore(stats.maxScore)}`} />
          <Stat label="Por revisar" value={stats.review} warn={stats.review > 0} />
        </div>
        {stats.pending > 0 && (
          <p className="warn">
            {stats.pending} matrícula(s) escaneada(s) más de una vez: elige cuál cuenta.
            Mientras tanto salen sin nota al exportar.
          </p>
        )}
        <div className="row">
          <button className="small" onClick={() => exportAs("xlsx")} disabled={!rows.length || !!busy}>
            {busy === "xlsx" ? "…" : "Exportar Excel"}
          </button>
          <button className="small secondary" onClick={() => exportAs("csv")} disabled={!rows.length || !!busy}>
            {busy === "csv" ? "…" : "Exportar CSV"}
          </button>
        </div>
      </div>

      {rows.length === 0 && (
        <div className="card">
          <h2>Todavía no hay hojas calificadas</h2>
          <Link className="btn" to={`/calificar?examen=${encodeURIComponent(id)}`}>Calificar</Link>
        </div>
      )}

      {Object.keys(pending).length > 0 && filter !== "review" && (
        <div className="card">
          <h2>Elegir cuál cuenta</h2>
          {Object.entries(pending).map(([studentId, group]) => (
            <div key={studentId} className="dup-group">
              <div className="k">Matrícula {studentId}</div>
              <div className="dup-options">
                {group.map((row) => (
                  <div key={row.id} className="dup-option">
                    <Link to={row.id}><ReviewImage resultId={row.id} className="thumb" /></Link>
                    <div><b>{fmtScore(row.final_score)}</b> / {fmtScore(row.max_score)}</div>
                    <div className="note">{fmtDate(row.scanned_at)}</div>
                    <button className="small" onClick={() => choose(row)} disabled={!!busy}>
                      {busy === row.id ? "…" : "Esta cuenta"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <div className="tabs">
          {FILTERS.map(([key, text]) => (
            <button key={key} className={filter === key ? "on" : ""}
                    onClick={() => setParams(key === "all" ? {} : { filtro: key }, { replace: true })}>
              {text}
            </button>
          ))}
        </div>
      )}

      {rows.length > 0 && shown.length === 0 && <p className="note">Nada por aquí.</p>}

      {shown.map((row) => {
        const status = resultStatus(row, pending);
        return (
          <Link key={row.id} to={row.id} className="card result-row">
            <div className="grow">
              <div className={`sid-line${row.student_id ? "" : " bad"}`}>{row.student_id_raw}</div>
              <div className="result-meta">
                <span className={`chip chip-${status}`}>{STATUS_TEXT[status]}</span>
                <span className="note">
                  {fmtDate(row.scanned_at)}
                  {row.ambiguous.length > 0 && ` · ${row.ambiguous.length} ambigua(s)`}
                </span>
              </div>
            </div>
            <div className="result-score">{fmtScore(row.final_score)}<span className="note"> / {fmtScore(row.max_score)}</span></div>
          </Link>
        );
      })}
    </>
  );
}

function Stat({ label, value, warn }) {
  return (
    <div className="stat">
      <div className={`stat-value${warn ? " warn" : ""}`}>{value}</div>
      <div className="note">{label}</div>
    </div>
  );
}

function BackLink() {
  return <Link className="back" to="/examenes">← Mis exámenes</Link>;
}
