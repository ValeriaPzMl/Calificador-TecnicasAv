"""
Administra los exámenes guardados en la BD (lo que hará la web en la fase 5).

Uso:
    python -m cli.exams add data/examples/ejemplo.json   # registrar
    python -m cli.exams list
    python -m cli.exams show ejemplo0001
    python -m cli.exams update examen_corregido.json     # mismo id
    python -m cli.exams sheet ejemplo0001 --preview      # PDF desde la BD
    python -m cli.exams delete ejemplo0001               # pide confirmación

`update` no deja cambiar la estructura (opciones, dígitos de matrícula,
preguntas por sección) si el examen ya tiene resultados; sí respuestas
correctas, puntos, pesos y nombres, y recalifica lo ya guardado.

`delete` borra también todos sus resultados.
"""

import argparse
import logging

from cli.logs import add_verbose_flag, setup_logging
from cli.make_sheet import write_sheet
from core.layout import PAGE_SIZES_MM, LayoutError, build_layout
from models.exam import ExamValidationError, load_exam
from storage import repository
from storage.db import open_session

logger = logging.getLogger(__name__)


def cmd_add(session, args) -> None:
    exam = load_exam(args.exam_path)
    build_layout(exam)  # que quepa en la hoja antes de guardarlo
    repository.create_exam(session, exam)
    print(f"OK. Examen '{exam.id}' registrado: {exam.name}, {exam.total_questions} preguntas")


def cmd_list(session, args) -> None:
    exams = repository.list_exams(session)
    if not exams:
        print("No hay exámenes registrados. Agrega uno con: python -m cli.exams add examen.json")
        return
    print(f"{'ID':<16} {'PREG':>4} {'RESULT':>6}  NOMBRE")
    for e in exams:
        print(f"{e.id:<16} {e.questions:>4} {e.results:>6}  {e.title}")


def cmd_show(session, args) -> None:
    exam = repository.get_exam(session, args.exam_id)
    if exam is None:
        raise repository.ExamNotFoundError(f"No existe el examen '{args.exam_id}'")
    n_results = repository.count_results(session, exam.id)
    print(f"{exam.name}  (id {exam.id})")
    print(f"  {exam.num_options} opciones, matrícula de {exam.student_id_digits} dígitos, "
          f"{n_results} resultados" + ("  [estructura bloqueada]" if n_results else ""))
    for s in exam.sections:
        weight = f"{s.weight:g}%" if s.weight is not None else f"{s.max_points:g} pts"
        answers = " ".join(f"{q.number}{q.correct_answer}" for q in s.questions)
        print(f"  {s.name} ({weight}): {answers}")


def cmd_update(session, args) -> None:
    exam = load_exam(args.exam_path)
    build_layout(exam)
    rescored = repository.update_exam(session, exam)
    print(f"OK. Examen '{exam.id}' actualizado."
          + (f" {rescored} resultados recalificados." if rescored else ""))


def cmd_sheet(session, args) -> None:
    exam = repository.get_exam(session, args.exam_id)
    if exam is None:
        raise repository.ExamNotFoundError(f"No existe el examen '{args.exam_id}'")
    write_sheet(exam, args.page, args.output, args.preview)


def cmd_delete(session, args) -> None:
    exam = repository.get_exam(session, args.exam_id)
    if exam is None:
        raise repository.ExamNotFoundError(f"No existe el examen '{args.exam_id}'")
    n_results = repository.count_results(session, exam.id)
    if not args.yes:
        print(f"Se borrará el examen '{exam.id}' ({exam.name}) "
              f"y sus {n_results} resultados. Esto no se puede deshacer.")
        if input("Escribe el id del examen para confirmar: ").strip() != exam.id:
            print("Cancelado.")
            return
    deleted = repository.delete_exam(session, exam.id)
    print(f"OK. Examen '{exam.id}' borrado junto con {deleted} resultados.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="Registrar un examen desde JSON")
    p.add_argument("exam_path")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="Listar exámenes")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="Ver un examen")
    p.add_argument("exam_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("update", help="Actualizar un examen desde JSON (mismo id)")
    p.add_argument("exam_path")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("sheet", help="Generar el PDF de un examen guardado")
    p.add_argument("exam_id")
    p.add_argument("-o", "--output")
    p.add_argument("--page", default="letter", choices=list(PAGE_SIZES_MM))
    p.add_argument("--preview", action="store_true")
    p.set_defaults(func=cmd_sheet)

    p = sub.add_parser("delete", help="Borrar un examen y sus resultados")
    p.add_argument("exam_id")
    p.add_argument("--yes", action="store_true", help="No pedir confirmación")
    p.set_defaults(func=cmd_delete)

    for subparser in sub.choices.values():
        add_verbose_flag(subparser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger.info("inicio: comando %s", args.command)
    session = open_session()
    try:
        args.func(session, args)
    except (ExamValidationError, LayoutError, repository.StorageError) as exc:
        logger.error("falló '%s': %s", args.command, type(exc).__name__)
        print(f"ERROR: {exc}")
        raise SystemExit(1)
    logger.info("fin")


if __name__ == "__main__":
    main()
