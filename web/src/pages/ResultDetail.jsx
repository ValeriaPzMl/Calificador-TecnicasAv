// Una hoja calificada: imagen de revisión, matrícula y respuestas que el
// profesor puede corregir (las ambiguas primero). Al guardar se recalifica.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ReviewImage from "../components/ReviewImage.jsx";
import { apiJson, jsonBody } from "../lib/api.js";
import { optionLetters } from "../lib/examForm.js";
import { log } from "../lib/log.js";
import {
  STATUS_TEXT, buildCorrection, checkStudentId, fmtDate, fmtScore, questionState,
} from "../lib/results.js";

export default function ResultDetail() {
  const { id, rid } = useParams();
  const navigate = useNavigate();
  const [exam, setExam] = useState(null);
  const [row, setRow] = useState(null);
  const [answers, setAnswers] = useState({});
  const [studentId, setStudentId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [showImage, setShowImage] = useState(true);
  const back = `/examenes/${encodeURIComponent(id)}/resultados`;

  function reset(r) {
    setRow(r);
    setAnswers({ ...r.answers });
    setStudentId(r.student_id ?? "");
  }

  useEffect(() => {
    setError("");
    Promise.all([apiJson(`/api/exams/${encodeURIComponent(id)}`), apiJson(`/api/results/${rid}`)])
      .then(([e, r]) => {
        log.info("hoja", rid, "de", e.id, `(${r.ambiguous.length} ambiguas)`);
        setExam(e);
        reset(r);
      })
      .catch((err) => { if (err.status !== 401) setError(err.message); });
  }, [id, rid]);

  const letters = exam ? optionLetters(exam.num_options) : [];
  const correction = row ? buildCorrection(row, answers, studentId) : null;
  const sidProblem = exam ? checkStudentId(studentId, exam.student_id_digits) : "";
  const changes = correction
    ? Object.keys(correction.answers ?? {}).length + (correction.student_id ? 1 : 0) : 0;
  const pendingAmbiguous = useMemo(
    () => (row ? row.ambiguous.filter((n) => (answers[n] ?? null) === (row.answers[n] ?? null)) : []),
    [row, answers]);

  async function save() {
    if (!correction || sidProblem) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      log.info("corrigiendo hoja", rid, correction);
      const r = await apiJson(`/api/results/${rid}`, jsonBody("PATCH", correction));
      const before = row.final_score;
      reset(r);
      setNotice(`Guardado. Nota: ${fmtScore(before)} → ${fmtScore(r.final_score)}.` +
        (r.duplicates ? ` Esta matrícula ya tenía ${r.duplicates} hoja(s): elige cuál cuenta.` : ""));
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function choose() {
    try {
      await apiJson(`/api/results/${rid}/choose`, { method: "POST" });
      log.info("hoja elegida", rid);
      setRow({ ...row, selected: true });
      setNotice("Esta hoja es la que cuenta para la matrícula.");
    } catch (err) {
      setError(err.message);
    }
  }

  async function remove() {
    if (!window.confirm(`¿Borrar la hoja de ${row.student_id_raw}? No se puede deshacer.`)) return;
    try {
      await apiJson(`/api/results/${rid}`, { method: "DELETE" });
      log.info("hoja borrada", rid);
      navigate(back, { replace: true });
    } catch (err) {
      setError(err.message);
    }
  }

  function jumpTo(number) {
    document.getElementById(`q-${number}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  if (!exam || !row) {
    return error
      ? <><Link className="back" to={back}>← Resultados</Link><p className="error">{error}</p></>
      : <div className="card center"><div className="spinner" /></div>;
  }

  return (
    <>
      <Link className="back" to={back}>← Resultados de {exam.name}</Link>
      <div className="card">
        <div className="row between">
          <div className="k" style={{ marginTop: 0 }}>Nota</div>
          {row.needs_review && <span className="chip chip-review">{STATUS_TEXT.review}</span>}
        </div>
        <p className="score">{fmtScore(row.final_score)} / {fmtScore(row.max_score)}</p>
        <ul className="sections">
          {row.sections.map((s) => (
            <li key={s.section_id}>
              {s.name}: {fmtScore(s.points)}/{fmtScore(s.max_points)} ({Math.round(s.percent)}%)
            </li>
          ))}
        </ul>
        <div className="note">Escaneada {fmtDate(row.scanned_at)}</div>

        <label>
          Matrícula{row.student_id ? "" : ` (se leyó "${row.student_id_raw}", incompleta)`}
          <input inputMode="numeric" value={studentId} maxLength={exam.student_id_digits}
                 className={row.student_id ? "" : "bad-input"}
                 placeholder={row.student_id_raw}
                 onChange={(e) => setStudentId(e.target.value)} />
        </label>
        {sidProblem && <div className="error">{sidProblem}</div>}

        {row.student_id && !row.selected && (
          <div className="banner warn-bg" style={{ marginTop: 10 }}>
            Esta matrícula tiene otra(s) hoja(s) y esta no es la que cuenta.
            <button className="small" onClick={choose}>Que cuente esta</button>
          </div>
        )}
      </div>

      {notice && <div className="banner">{notice}</div>}
      {error && <p className="error">{error}</p>}

      {pendingAmbiguous.length > 0 && (
        <div className="card">
          <h2>Por revisar</h2>
          <p className="note">El lector no pudo decidir estas preguntas (valen 0). Mira la imagen y elige la respuesta.</p>
          <div className="row">
            {pendingAmbiguous.map((n) => (
              <button key={n} className="small secondary chip-btn" onClick={() => jumpTo(n)}>{n}</button>
            ))}
          </div>
        </div>
      )}

      {row.has_image && (
        <div className="card">
          <div className="row between">
            <div className="k" style={{ marginTop: 0 }}>
              Imagen de revisión (lectura original): verde correcta · rojo incorrecta · naranja ambigua · gris en blanco
            </div>
            <button className="small secondary" onClick={() => setShowImage(!showImage)}>
              {showImage ? "Ocultar" : "Ver"}
            </button>
          </div>
          {showImage && <ReviewImage resultId={rid} />}
        </div>
      )}

      {exam.sections.map((section) => (
        <div key={section.id} className="card section-card">
          <h2>{section.name}</h2>
          <div className="questions">
            {section.questions.map((q) => {
              const answer = answers[q.number] ?? null;
              const state = questionState(row, q.number, answer, q.correct_answer);
              const changed = (row.answers[q.number] ?? null) !== answer;
              return (
                <div key={q.number} id={`q-${q.number}`} className={`question q-${state}`}>
                  <span className="num">{changed ? "•" : ""}{q.number}</span>
                  {letters.map((letter) => (
                    <button key={letter} type="button"
                            className={`opt${answer === letter ? " on" : ""}${letter === q.correct_answer ? " key" : ""}`}
                            onClick={() => setAnswers({ ...answers, [q.number]: letter })}>
                      {letter}
                    </button>
                  ))}
                  <button type="button" title="En blanco"
                          className={`opt blank-opt${answer == null ? " on" : ""}`}
                          onClick={() => setAnswers({ ...answers, [q.number]: null })}>
                    –
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <p className="note">El círculo con borde verde es la respuesta correcta; • = cambiada sin guardar.</p>

      <div className="sticky-actions row">
        <button onClick={save} disabled={!correction || !!sidProblem || saving}>
          {saving ? "Guardando…" : changes ? `Guardar ${changes} cambio(s)` : "Sin cambios"}
        </button>
        {correction && <button className="secondary" onClick={() => reset(row)}>Descartar</button>}
        <button className="danger" onClick={remove}>Borrar hoja</button>
      </div>
    </>
  );
}
