// Crear o editar un examen. La lógica (numerar, pegar respuestas, validar,
// armar el JSON) está en lib/examForm.js; aquí solo la pantalla.
//
// Un examen con resultados queda "bloqueado": no se puede cambiar lo que
// mueve burbujas en la hoja impresa (opciones, dígitos de matrícula,
// cuántas preguntas y secciones hay). Nombres, respuestas, puntos y pesos
// sí, y al guardar se recalifica lo ya calificado.
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { apiFetch, apiJson, ApiError, jsonBody } from "../lib/api.js";
import {
  MAX_DIGITS, MAX_OPTIONS, MIN_DIGITS, MIN_OPTIONS, apiErrorText, applyAnswers, clearInvalidAnswers,
  emptyForm, examFromForm, firstNumbers, formFromExam, newSection, optionLetters, parseAnswers,
  setQuestionCount, validateForm, weightTotal,
} from "../lib/examForm.js";
import { log } from "../lib/log.js";
import { savedPage } from "./Exams.jsx";

export default function ExamEditor() {
  const { id } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(id);
  const [form, setForm] = useState(editing ? null : emptyForm);
  const [meta, setMeta] = useState({ locked: false, results: 0 });
  const [loadError, setLoadError] = useState("");
  const [problems, setProblems] = useState([]);
  const [showMissing, setShowMissing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState({ url: null, busy: false, error: "" });

  useEffect(() => {
    if (!editing) return;
    apiJson(`/api/exams/${encodeURIComponent(id)}`)
      .then((exam) => {
        log.info("editando examen", exam.id, exam.locked ? "(bloqueado)" : "");
        setForm(formFromExam(exam));
        setMeta({ locked: exam.locked, results: exam.results });
      })
      .catch((err) => setLoadError(err.message));
  }, [id, editing]);

  useEffect(() => () => preview.url && URL.revokeObjectURL(preview.url), [preview.url]);

  if (loadError) return <div className="card"><p className="error">{loadError}</p></div>;
  if (!form) return <div className="card center"><div className="spinner" /></div>;

  const locked = meta.locked;
  const { missing } = validateForm(form);
  const missingSet = new Set(showMissing ? missing : []);
  const starts = firstNumbers(form.sections);
  const total = form.sections.reduce((n, s) => n + s.questions.length, 0);

  const update = (patch) => setForm((f) => ({ ...f, ...patch }));
  const updateSection = (index, section) =>
    setForm((f) => ({ ...f, sections: f.sections.map((s, i) => (i === index ? section : s)) }));

  function changeOptions(value) {
    setForm((f) => clearInvalidAnswers({ ...f, numOptions: Number(value) }));
  }

  function moveSection(index, delta) {
    setForm((f) => {
      const sections = f.sections.slice();
      const [s] = sections.splice(index, 1);
      sections.splice(index + delta, 0, s);
      return { ...f, sections };
    });
  }

  async function showPreview() {
    setPreview((p) => ({ ...p, busy: true, error: "" }));
    const params = new URLSearchParams({ page: savedPage() });
    if (editing) params.set("exam_id", id);
    try {
      const resp = await apiFetch(`/api/exams/preview?${params}`, jsonBody("POST", examFromForm(form)));
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new ApiError(apiErrorText(data.detail, resp.status), resp.status);
      }
      const url = URL.createObjectURL(await resp.blob());
      log.info("vista previa lista");
      setPreview({ url, busy: false, error: "" });
    } catch (err) {
      setPreview({ url: null, busy: false, error: err.message });
    }
  }

  async function save() {
    const check = validateForm(form);
    setProblems(check.problems);
    setShowMissing(true);
    if (check.problems.length) {
      log.info("no se guarda: ", check.problems);
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
      return;
    }
    setSaving(true);
    try {
      const body = examFromForm(form);
      if (editing) {
        const r = await apiJson(`/api/exams/${encodeURIComponent(id)}`, jsonBody("PUT", body));
        const rescored = r.rescored ? ` Se recalificaron ${r.rescored} resultado(s).` : "";
        navigate("/examenes", { state: { examId: id, notice: `Examen guardado.${rescored}` } });
      } else {
        const r = await apiJson("/api/exams", jsonBody("POST", body));
        log.info("examen creado", r.id);
        navigate("/examenes", { state: { examId: r.id,
          notice: `Examen "${body.name}" creado. Descarga su PDF para imprimir las hojas.` } });
      }
    } catch (err) {
      setProblems([err.message]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h1>{editing ? "Editar examen" : "Nuevo examen"}</h1>
      {locked && (
        <div className="banner warn-bg">
          Este examen ya tiene {meta.results} resultado(s), así que las hojas impresas no pueden
          cambiar: no se pueden mover opciones, dígitos de matrícula ni cuántas preguntas o
          secciones hay. Sí puedes corregir nombres, respuestas, puntos y porcentajes, y se
          recalificará lo ya calificado.
        </div>
      )}

      <div className="card">
        <label htmlFor="name">Nombre del examen</label>
        <input id="name" value={form.name} placeholder="Ej. Química - Parcial 2" maxLength={200}
               onChange={(e) => update({ name: e.target.value })} />
        <div className="grid-2">
          <div>
            <label htmlFor="opts">Opciones por pregunta</label>
            <select id="opts" value={form.numOptions} disabled={locked}
                    onChange={(e) => changeOptions(e.target.value)}>
              {Array.from({ length: MAX_OPTIONS - MIN_OPTIONS + 1 }, (_, i) => i + MIN_OPTIONS).map((n) => (
                <option key={n} value={n}>{n} ({optionLetters(n).join(", ")})</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="digits">Dígitos de la matrícula</label>
            <input id="digits" type="number" min={MIN_DIGITS} max={MAX_DIGITS} value={form.studentIdDigits}
                   disabled={locked} onChange={(e) => update({ studentIdDigits: e.target.value })} />
          </div>
        </div>
        <label>Cómo se calcula la nota</label>
        <label className="check">
          <input type="radio" name="mode" checked={form.mode === "points"} onChange={() => update({ mode: "points" })} />
          Suma de puntos
        </label>
        <label className="check">
          <input type="radio" name="mode" checked={form.mode === "weights"} onChange={() => update({ mode: "weights" })} />
          Porcentaje por sección (sobre 100)
        </label>
        {form.mode === "weights" && (
          <p className={weightTotal(form) === 100 ? "note ok" : "note warn"}>
            Los porcentajes suman {weightTotal(form)}%{weightTotal(form) === 100 ? "." : " (deben sumar 100%)."}
          </p>
        )}
      </div>

      {form.sections.map((section, i) => (
        <SectionCard key={section.key} section={section} index={i} count={form.sections.length}
                     start={starts[i]} numOptions={form.numOptions} mode={form.mode} locked={locked}
                     missing={missingSet}
                     onChange={(s) => updateSection(i, s)}
                     onRemove={() => setForm((f) => ({ ...f, sections: f.sections.filter((_, j) => j !== i) }))}
                     onMove={(delta) => moveSection(i, delta)} />
      ))}

      {!locked && (
        <button className="secondary" onClick={() => setForm((f) => ({ ...f, sections: [...f.sections, newSection(f.sections)] }))}>
          + Agregar sección
        </button>
      )}

      <div className="card preview">
        <div className="row between">
          <h2>Hoja de respuestas</h2>
          <button className="small secondary" onClick={showPreview} disabled={preview.busy}>
            {preview.busy ? "Generando…" : preview.url ? "Actualizar vista previa" : "Ver vista previa"}
          </button>
        </div>
        <p className="note">{total} preguntas. La vista previa también revisa que todo quepa en una hoja.</p>
        {preview.error && <p className="error">{preview.error}</p>}
        {preview.url && <img src={preview.url} alt="Vista previa de la hoja de respuestas" />}
      </div>

      <div className="sticky-actions">
        {problems.length > 0 && (
          <div className="card">
            <strong className="error">Antes de guardar:</strong>
            <ul className="problems error">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        )}
        <div className="row">
          <button className="secondary grow" onClick={() => navigate("/examenes")}>Cancelar</button>
          <button className="grow" onClick={save} disabled={saving}>
            {saving ? "Guardando…" : editing ? "Guardar cambios" : "Crear examen"}
          </button>
        </div>
      </div>
    </>
  );
}

function SectionCard({ section, index, count, start, numOptions, mode, locked, missing, onChange, onRemove, onMove }) {
  const [paste, setPaste] = useState("");
  const [pasteMsg, setPasteMsg] = useState("");
  const letters = optionLetters(numOptions);
  const n = section.questions.length;

  function applyPaste() {
    let { letters: pasted, invalid } = parseAnswers(paste, numOptions);
    if (!pasted.length) return setPasteMsg("No encontré letras en el texto.");
    let cut = 0;
    if (locked && pasted.length > n) { cut = pasted.length - n; pasted = pasted.slice(0, n); }
    onChange(applyAnswers(section, pasted, numOptions));
    const parts = [`Se llenaron ${pasted.length} respuesta(s).`];
    if (invalid.length) parts.push(`Letras que no son opciones (${letters.join(", ")}) en la posición ${invalid.join(", ")}: quedaron sin respuesta.`);
    if (cut) parts.push(`Sobraban ${cut} letra(s); no se agregan preguntas porque el examen ya tiene resultados.`);
    setPasteMsg(parts.join(" "));
    setPaste("");
  }

  const setQuestion = (j, patch) =>
    onChange({ ...section, questions: section.questions.map((q, k) => (k === j ? { ...q, ...patch } : q)) });

  return (
    <div className="card section-card">
      <div className="row between">
        <h2>Sección {index + 1}</h2>
        {!locked && (
          <div className="row">
            <button className="small secondary" disabled={index === 0} onClick={() => onMove(-1)} aria-label="Subir">↑</button>
            <button className="small secondary" disabled={index === count - 1} onClick={() => onMove(1)} aria-label="Bajar">↓</button>
            <button className="small danger" disabled={count === 1} onClick={onRemove}>Quitar</button>
          </div>
        )}
      </div>
      <label>Nombre</label>
      <input value={section.name} onChange={(e) => onChange({ ...section, name: e.target.value })} />
      <div className="grid-2">
        <div>
          <label>Número de preguntas</label>
          <input type="number" min={1} max={200} value={n} disabled={locked}
                 onChange={(e) => onChange(setQuestionCount(section, e.target.value))} />
        </div>
        {mode === "weights" ? (
          <div>
            <label>Porcentaje de la nota (%)</label>
            <input type="number" min={0} max={100} step="any" value={section.weight}
                   onChange={(e) => onChange({ ...section, weight: e.target.value })} />
          </div>
        ) : <div />}
        {!section.perQuestionPoints && (
          <div>
            <label>Puntos por pregunta</label>
            <input type="number" min={0} step="any" value={section.defaultPoints}
                   onChange={(e) => onChange({ ...section, defaultPoints: e.target.value })} />
          </div>
        )}
      </div>
      <label className="check">
        <input type="checkbox" checked={section.perQuestionPoints}
               onChange={(e) => onChange({
                 ...section,
                 perQuestionPoints: e.target.checked,
                 questions: section.questions.map((q) => ({ ...q, points: section.defaultPoints })),
               })} />
        Cada pregunta vale distinto
      </label>

      <label>Pegar respuestas (ej. ABDCA o 1A 2B 3D)</label>
      <div className="row">
        <input className="grow" value={paste} placeholder={letters.slice(0, 4).join("") + "…"}
               onChange={(e) => setPaste(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); applyPaste(); } }} />
        <button className="small" onClick={applyPaste} disabled={!paste.trim()}>Llenar</button>
      </div>
      {pasteMsg && <p className="note">{pasteMsg}</p>}

      <div className="questions">
        {section.questions.map((q, j) => {
          const number = start + j;
          return (
            <div key={j} className={`question${missing.has(number) ? " missing" : ""}`}>
              <span className="num">{number}.</span>
              {letters.map((l) => (
                <button key={l} type="button" className={`opt${q.answer === l ? " on" : ""}`}
                        aria-pressed={q.answer === l} aria-label={`Pregunta ${number}: ${l}`}
                        onClick={() => setQuestion(j, { answer: q.answer === l ? "" : l })}>
                  {l}
                </button>
              ))}
              {section.perQuestionPoints && (
                <input className="points" type="number" min={0} step="any" value={q.points}
                       aria-label={`Puntos de la pregunta ${number}`}
                       onChange={(e) => setQuestion(j, { points: e.target.value })} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
