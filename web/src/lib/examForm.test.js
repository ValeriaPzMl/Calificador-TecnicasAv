import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  applyAnswers, apiErrorText, clearInvalidAnswers, emptyForm, examFromForm, firstNumbers,
  formFromExam, newSection, parseAnswers, setQuestionCount, validateForm,
} from "./examForm.js";

const example = (name) =>
  JSON.parse(readFileSync(new URL(`../../../data/examples/${name}.json`, import.meta.url), "utf8"));

function filledForm() {
  const form = emptyForm();
  form.name = "Química";
  form.sections = [applyAnswers(setQuestionCount(form.sections[0], 3), ["A", "B", "C"], 4)];
  return form;
}

describe("parseAnswers", () => {
  it.each([
    ["ABDCA", "ABDCA"],
    ["a b d", "ABD"],
    ["A,B,D", "ABD"],
    ["1A 2B 3D", "ABD"],
    ["1. a\n2. b\n3. c", "ABC"],
  ])("%j", (text, expected) => {
    expect(parseAnswers(text, 4).letters.join("")).toBe(expected);
  });

  it("marca letras que no son opciones", () => {
    expect(parseAnswers("ABEZ", 4).invalid).toEqual([3, 4]);
    expect(parseAnswers("ABE", 5).invalid).toEqual([]);
  });
});

describe("applyAnswers", () => {
  it("llena desde la primera y agrega preguntas si sobran letras", () => {
    const s = applyAnswers(setQuestionCount(newSection(), 2), ["B", "C", "D"], 4);
    expect(s.questions.map((q) => q.answer)).toEqual(["B", "C", "D"]);
  });

  it("no pone letras inválidas y deja las demás preguntas", () => {
    const s = applyAnswers(setQuestionCount(newSection(), 4), ["A", "Z"], 4);
    expect(s.questions.map((q) => q.answer)).toEqual(["A", "", "", ""]);
  });
});

describe("numeración y JSON", () => {
  it("numera de corrido entre secciones", () => {
    const form = emptyForm();
    form.sections = [setQuestionCount(form.sections[0], 3), newSection(form.sections, 2)];
    expect(firstNumbers(form.sections)).toEqual([1, 4]);
    const body = examFromForm(form);
    expect(body.sections[1].id).toBe("s2");
    expect(body.sections[1].questions.map((q) => q.number)).toEqual([4, 5]);
  });

  it.each(["ingles01", "ejemplo", "biologia01", "historia01"])(
    "ida y vuelta con data/examples/%s.json", (name) => {
      const exam = example(name);
      const body = examFromForm(formFromExam(exam));
      const { id, ...rest } = exam;
      const normalize = (e) => ({
        ...e,
        sections: e.sections.map((s) => ({ ...s, weight: s.weight ?? null })),
      });
      expect(body).toEqual(normalize(rest));
      expect(validateForm(formFromExam(exam)).problems).toEqual([]);
    });
});

describe("validateForm", () => {
  it("un examen completo no tiene problemas", () => {
    expect(validateForm(filledForm()).problems).toEqual([]);
  });

  it("avisa de respuestas faltantes con su número", () => {
    const form = filledForm();
    form.sections[0].questions[1].answer = "";
    const { problems, missing } = validateForm(form);
    expect(missing).toEqual([2]);
    expect(problems.join(" ")).toMatch(/Faltan respuestas en 1 pregunta/);
  });

  it("los porcentajes deben sumar 100", () => {
    const form = filledForm();
    form.mode = "weights";
    form.sections[0].weight = 90;
    expect(validateForm(form).problems.join(" ")).toMatch(/suman 90%/);
    form.sections[0].weight = 100;
    expect(validateForm(form).problems).toEqual([]);
  });

  it("pide nombre y puntos > 0", () => {
    const form = filledForm();
    form.name = " ";
    form.sections[0].defaultPoints = 0;
    const text = validateForm(form).problems.join(" ");
    expect(text).toMatch(/nombre al examen/);
    expect(text).toMatch(/mayores que 0/);
  });

  it("bajar opciones borra las respuestas que ya no existen", () => {
    const form = filledForm();
    form.numOptions = 2;
    const fixed = clearInvalidAnswers(form);
    expect(fixed.sections[0].questions.map((q) => q.answer)).toEqual(["A", "B", ""]);
  });
});

describe("apiErrorText", () => {
  it("entiende los formatos de error de la API", () => {
    expect(apiErrorText("No existe", 404)).toBe("No existe");
    expect(apiErrorText({ message: "El examen tiene errores.", problems: ["a", "b"] }, 422))
      .toBe("El examen tiene errores.\n• a\n• b");
    expect(apiErrorText([{ msg: "field required" }], 422)).toBe("field required");
    expect(apiErrorText(undefined, 500)).toBe("Error 500");
  });
});
