// Lógica del editor de exámenes, sin React (se prueba con vitest).
//
// El formulario guarda el examen como lo piensa el profesor: secciones con
// un número de preguntas y una respuesta por pregunta. Al guardar se
// convierte al formato de la API (el mismo de data/examples/*.json), con
// las preguntas numeradas de corrido entre secciones.
import { log } from "./log.js";

export const LETTERS = "ABCDEFGH";
export const MIN_OPTIONS = 2;
export const MAX_OPTIONS = 8;
export const MIN_DIGITS = 1;
export const MAX_DIGITS = 12;

export function optionLetters(numOptions) {
  return LETTERS.slice(0, numOptions).split("");
}

function newQuestion(points) {
  return { answer: "", points };
}

function nextSectionKey(sections) {
  const used = sections.map((s) => Number(/^s(\d+)$/.exec(s.key)?.[1] ?? 0));
  return `s${Math.max(0, ...used) + 1}`;
}

export function newSection(sections = [], questions = 10) {
  const key = nextSectionKey(sections);
  return {
    key,
    name: `Sección ${sections.length + 1}`,
    weight: "",
    defaultPoints: 1,
    perQuestionPoints: false,
    questions: Array.from({ length: questions }, () => newQuestion(1)),
  };
}

export function emptyForm() {
  return { name: "", numOptions: 4, studentIdDigits: 6, mode: "points", sections: [newSection()] };
}

// Examen de la API (GET /api/exams/{id}) -> formulario.
export function formFromExam(exam) {
  const weighted = exam.sections.some((s) => s.weight != null);
  log.debug("formulario desde examen", exam.id, weighted ? "con pesos" : "por puntos");
  return {
    name: exam.name,
    numOptions: exam.num_options,
    studentIdDigits: exam.student_id_digits,
    mode: weighted ? "weights" : "points",
    sections: exam.sections.map((s) => {
      const points = s.questions.map((q) => q.points);
      const same = points.every((p) => p === points[0]);
      return {
        key: s.id,
        name: s.name,
        weight: s.weight ?? "",
        defaultPoints: points[0] ?? 1,
        perQuestionPoints: !same,
        questions: s.questions.map((q) => ({ answer: q.correct_answer, points: q.points })),
      };
    }),
  };
}

// Número de la primera pregunta de cada sección (de corrido: 1..n).
export function firstNumbers(sections) {
  const starts = [];
  let next = 1;
  for (const s of sections) {
    starts.push(next);
    next += s.questions.length;
  }
  return starts;
}

// Formulario -> cuerpo para POST/PUT /api/exams.
export function examFromForm(form) {
  const starts = firstNumbers(form.sections);
  return {
    name: form.name.trim(),
    num_options: Number(form.numOptions),
    student_id_digits: Number(form.studentIdDigits),
    sections: form.sections.map((s, i) => ({
      id: s.key,
      name: s.name.trim(),
      weight: form.mode === "weights" ? Number(s.weight) : null,
      questions: s.questions.map((q, j) => ({
        number: starts[i] + j,
        correct_answer: q.answer,
        points: Number(s.perQuestionPoints ? q.points : s.defaultPoints),
      })),
    })),
  };
}

// Cambia cuántas preguntas tiene una sección (agrega o quita al final).
export function setQuestionCount(section, count) {
  const n = Math.max(1, Math.min(200, Math.floor(Number(count) || 1)));
  const questions = section.questions.slice(0, n);
  while (questions.length < n) questions.push(newQuestion(section.defaultPoints));
  return { ...section, questions };
}

// Texto pegado -> lista de letras. Acepta "ABDCA", "A B D", "A,B,D",
// "1A 2B 3D", "1. a  2. b"... Los números se ignoran: cuenta el orden.
// `invalid` son las posiciones (desde 1) con letras que no son opciones.
export function parseAnswers(text, numOptions) {
  const valid = optionLetters(numOptions);
  const letters = text.toUpperCase().replace(/[^A-Z]/g, "").split("").filter(Boolean);
  const invalid = [];
  letters.forEach((l, i) => { if (!valid.includes(l)) invalid.push(i + 1); });
  log.debug(`respuestas pegadas: ${letters.length} letras, ${invalid.length} inválidas`);
  return { letters, invalid };
}

// Llena las respuestas de la sección desde la primera pregunta; si hay más
// letras que preguntas, agrega preguntas. Las letras inválidas no se ponen.
export function applyAnswers(section, letters, numOptions) {
  const valid = optionLetters(numOptions);
  const withRoom = setQuestionCount(section, Math.max(section.questions.length, letters.length));
  const questions = withRoom.questions.map((q, i) =>
    i < letters.length ? { ...q, answer: valid.includes(letters[i]) ? letters[i] : "" } : q);
  return { ...withRoom, questions };
}

// Al bajar el número de opciones, se borran las respuestas que ya no existen.
export function clearInvalidAnswers(form) {
  const valid = optionLetters(form.numOptions);
  return {
    ...form,
    sections: form.sections.map((s) => ({
      ...s,
      questions: s.questions.map((q) => (valid.includes(q.answer) ? q : { ...q, answer: "" })),
    })),
  };
}

export function weightTotal(form) {
  return form.sections.reduce((sum, s) => sum + (Number(s.weight) || 0), 0);
}

// Problemas antes de guardar, en palabras del profesor. `missing` son los
// números de las preguntas sin respuesta.
export function validateForm(form) {
  const problems = [];
  const missing = [];
  if (!form.name.trim()) problems.push("Ponle un nombre al examen.");
  const opts = Number(form.numOptions);
  if (!(opts >= MIN_OPTIONS && opts <= MAX_OPTIONS))
    problems.push(`Las opciones por pregunta van de ${MIN_OPTIONS} a ${MAX_OPTIONS}.`);
  const digits = Number(form.studentIdDigits);
  if (!(Number.isInteger(digits) && digits >= MIN_DIGITS && digits <= MAX_DIGITS))
    problems.push(`La matrícula puede tener de ${MIN_DIGITS} a ${MAX_DIGITS} dígitos.`);
  if (!form.sections.length) problems.push("Agrega al menos una sección.");

  const starts = firstNumbers(form.sections);
  form.sections.forEach((s, i) => {
    const label = s.name.trim() || `la sección ${i + 1}`;
    if (!s.name.trim()) problems.push(`La sección ${i + 1} no tiene nombre.`);
    const points = s.perQuestionPoints ? s.questions.map((q) => Number(q.points)) : [Number(s.defaultPoints)];
    if (points.some((p) => !(p > 0))) problems.push(`En ${label}, los puntos deben ser mayores que 0.`);
    if (form.mode === "weights" && !(Number(s.weight) > 0))
      problems.push(`Ponle un porcentaje a ${label}.`);
    s.questions.forEach((q, j) => { if (!q.answer) missing.push(starts[i] + j); });
  });
  if (form.mode === "weights" && form.sections.length) {
    const total = weightTotal(form);
    if (Math.abs(total - 100) > 1e-6) problems.push(`Los porcentajes suman ${total}%; deben sumar 100%.`);
  }
  if (missing.length) {
    const list = missing.length > 8 ? `${missing.slice(0, 8).join(", ")}…` : missing.join(", ");
    problems.push(`Faltan respuestas en ${missing.length} pregunta(s): ${list}.`);
  }
  return { problems, missing };
}

// Mensaje de error de la API (string o {message, problems}) -> texto.
export function apiErrorText(detail, status) {
  if (!detail) return `Error ${status}`;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg ?? String(d)).join("\n");
  return [detail.message, ...(detail.problems ?? [])].filter(Boolean).join("\n• ");
}
