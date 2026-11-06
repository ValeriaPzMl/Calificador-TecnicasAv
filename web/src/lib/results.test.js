import { describe, expect, it } from "vitest";
import {
  buildCorrection, checkStudentId, duplicateGroups, filterResults, pendingGroups,
  questionState, resultStatus, summary,
} from "./results.js";

let n = 0;
const sheet = (over = {}) => ({
  id: `r${++n}`, student_id: "111111", student_id_raw: "111111", final_score: 80,
  max_score: 100, answers: { 1: "A", 2: null, 3: "B" }, ambiguous: [], blank: [2],
  selected: true, needs_review: false, scanned_at: "2026-11-05T10:00:00", ...over,
});

describe("duplicados y estado", () => {
  const ok = sheet();
  const dupA = sheet({ student_id: "222222", selected: false });
  const dupB = sheet({ student_id: "222222", selected: false });
  const unread = sheet({ student_id: null, student_id_raw: "1?1111", needs_review: true });
  const rows = [ok, dupA, dupB, unread];

  it("agrupa por matrícula y deja fuera las ilegibles", () => {
    expect(Object.keys(duplicateGroups(rows))).toEqual(["222222"]);
    expect(Object.keys(pendingGroups(rows))).toEqual(["222222"]);
    expect(pendingGroups([ok, { ...dupA, selected: true }, dupB])).toEqual({});
  });

  it("estado de cada hoja", () => {
    const pending = pendingGroups(rows);
    expect(rows.map((r) => resultStatus(r, pending))).toEqual(["ok", "pending", "pending", "review"]);
    const chosen = [{ ...dupA, selected: true }, dupB];
    expect(chosen.map((r) => resultStatus(r, pendingGroups(chosen)))).toEqual(["ok", "discarded"]);
  });

  it("resumen como la exportación", () => {
    expect(summary(rows)).toMatchObject({ sheets: 4, counted: 2, average: 80, review: 1, pending: 1 });
    expect(summary([]).average).toBeNull();
  });

  it("filtros", () => {
    expect(filterResults(rows, "review")).toEqual([unread]);
    expect(filterResults(rows, "duplicates")).toEqual([dupA, dupB]);
    expect(filterResults(rows, "all")).toBe(rows);
  });
});

describe("corrección", () => {
  const row = sheet({ answers: { 1: "A", 2: null, 3: null }, ambiguous: [3], needs_review: true });

  it("estado de pregunta según el borrador", () => {
    expect(questionState(row, 3, null, "C")).toBe("ambiguous");
    expect(questionState(row, 3, "C", "C")).toBe("correct");
    expect(questionState(row, 1, "A", "B")).toBe("wrong");
    expect(questionState(row, 2, null, "B")).toBe("blank");
  });

  it("manda solo lo que cambió", () => {
    expect(buildCorrection(row, { ...row.answers }, "111111")).toBeNull();
    expect(buildCorrection(row, { ...row.answers, 3: "C", 1: null }, " 111111 "))
      .toEqual({ answers: { 1: null, 3: "C" } });
    expect(buildCorrection(row, { ...row.answers }, "204517")).toEqual({ student_id: "204517" });
  });

  it("revisa la matrícula", () => {
    expect(checkStudentId("204517", 6)).toBe("");
    expect(checkStudentId("2045", 6)).toMatch(/6 dígitos/);
    expect(checkStudentId("20451a", 6)).toMatch(/números/);
  });
});
