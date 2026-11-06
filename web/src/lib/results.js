// Lógica pura de la página de Resultados (sin React, se prueba con vitest).
// Una hoja es lo que regresa GET /api/exams/:id/results (api/results.py):
// { id, student_id, student_id_raw, final_score, max_score, answers: {"1": "A"},
//   ambiguous: [3], blank: [7], selected, needs_review, scanned_at, ... }
import { log } from "./log.js";

export const STATUS_TEXT = {
  ok: "Lista",
  review: "Revisar",
  pending: "Elegir cuál cuenta",
  discarded: "No cuenta",
};

// Hojas agrupadas por matrícula, solo las matrículas con más de una hoja.
export function duplicateGroups(rows) {
  const groups = {};
  for (const r of rows) {
    if (r.student_id != null) (groups[r.student_id] ||= []).push(r);
  }
  return Object.fromEntries(Object.entries(groups).filter(([, g]) => g.length > 1));
}

// Matrículas con varias hojas y ninguna elegida todavía (como
// repository.pending_duplicates).
export function pendingGroups(rows) {
  return Object.fromEntries(
    Object.entries(duplicateGroups(rows)).filter(([, g]) => !g.some((r) => r.selected)));
}

// ok | review (ambiguas o matrícula incompleta) | pending (duplicado sin
// elegir) | discarded (duplicado: cuenta otra hoja).
export function resultStatus(row, pending) {
  if (row.student_id != null && pending[row.student_id]) return "pending";
  if (row.student_id != null && !row.selected) return "discarded";
  if (row.needs_review) return "review";
  return "ok";
}

// Lo mismo que cuenta la exportación: una hoja por matrícula elegida, más
// las de matrícula ilegible; las pendientes no tienen nota todavía.
export function summary(rows) {
  const pending = pendingGroups(rows);
  const statuses = rows.map((r) => resultStatus(r, pending));
  const counted = rows.filter((_, i) => statuses[i] === "ok" || statuses[i] === "review");
  const average = counted.length
    ? counted.reduce((sum, r) => sum + r.final_score, 0) / counted.length
    : null;
  const result = {
    sheets: rows.length,
    counted: counted.length,
    average,
    maxScore: rows[0]?.max_score ?? null,
    review: statuses.filter((s) => s === "review").length,
    pending: Object.keys(pending).length,
  };
  log.debug("resumen de resultados", result);
  return result;
}

export function filterResults(rows, filter) {
  const pending = pendingGroups(rows);
  if (filter === "review") return rows.filter((r) => resultStatus(r, pending) === "review");
  if (filter === "duplicates") {
    const dupes = duplicateGroups(rows);
    return rows.filter((r) => r.student_id != null && dupes[r.student_id]);
  }
  return rows;
}

// Estado de una pregunta en el detalle. `answer` es la del borrador.
export function questionState(row, number, answer, correct) {
  const key = String(number);
  const changed = (row.answers[key] ?? null) !== (answer ?? null);
  if (!changed && row.ambiguous.includes(number)) return "ambiguous";
  if (answer == null) return "blank";
  return answer === correct ? "correct" : "wrong";
}

// Lo que se manda a PATCH /api/results/:id: solo lo que cambió, o null si nada.
export function buildCorrection(row, draftAnswers, draftStudentId) {
  const answers = {};
  for (const [key, value] of Object.entries(draftAnswers)) {
    if ((row.answers[key] ?? null) !== (value ?? null)) answers[key] = value ?? null;
  }
  const body = {};
  if (Object.keys(answers).length) body.answers = answers;
  const sid = (draftStudentId ?? "").trim();
  if (sid && sid !== (row.student_id ?? "")) body.student_id = sid;
  return Object.keys(body).length ? body : null;
}

export function checkStudentId(value, digits) {
  const sid = (value ?? "").trim();
  if (!sid) return "";
  if (!/^[0-9]+$/.test(sid)) return "Solo números.";
  if (sid.length !== digits) return `Debe tener ${digits} dígitos.`;
  return "";
}

export const fmtScore = (n) => (n == null ? "—" : (Math.round(n * 100) / 100).toString());

export function fmtDate(iso) {
  // SQLite regresa la fecha sin zona: es UTC
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return date.toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" });
}
