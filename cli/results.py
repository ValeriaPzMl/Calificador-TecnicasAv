"""
Consulta y exporta los resultados guardados de un examen.

Uso:
    python -m cli.results list ejemplo0001
    python -m cli.results duplicates ejemplo0001     # matrículas por elegir
    python -m cli.results choose <id del resultado>  # el profesor elige
    python -m cli.results export ejemplo0001 -o resultados.csv

`export` incluye solo un resultado por matrícula (el elegido). Las
matrículas escaneadas más de una vez sin elegir salen marcadas y sin nota
hasta que se elija.
"""

import argparse
import logging
import uuid
from pathlib import Path

from cli.logs import add_verbose_flag, setup_logging
from storage import repository
from storage.db import open_session
from storage.export import results_dataframe
from storage.tables import local_time

EXPORT_DIR = Path(__file__).resolve().parent.parent / "data" / "results"

logger = logging.getLogger(__name__)


def _row_line(r) -> str:
    flags = []
    if r.needs_review:
        flags.append("REVISAR")
    if not r.selected:
        flags.append("sin elegir")
    return (f"{r.id}  {r.student_id_raw:<12} {r.final_score:>6g}/{r.max_score:<4g} "
            f"{local_time(r.scanned_at):%Y-%m-%d %H:%M}  {' '.join(flags)}")


def cmd_list(session, args) -> None:
    rows = repository.list_results(session, args.exam_id)
    if not rows:
        print(f"El examen '{args.exam_id}' no tiene resultados.")
        return
    print(f"{'RESULTADO':<36}  {'MATRÍCULA':<12} {'NOTA':>11} {'ESCANEADO':<16}")
    for r in rows:
        print(_row_line(r))


def cmd_duplicates(session, args) -> None:
    pending = repository.pending_duplicates(session, args.exam_id)
    if not pending:
        print("No hay matrículas duplicadas pendientes.")
        return
    for student_id, rows in pending.items():
        print(f"Matrícula {student_id}: {len(rows)} resultados")
        for r in rows:
            print(f"  {_row_line(r)}")
            if r.image_path:
                print(f"      revisión: {r.image_path}")
    print("\nElige con: python -m cli.results choose <id del resultado>")


def cmd_choose(session, args) -> None:
    row = repository.choose_result(session, uuid.UUID(args.result_id))
    print(f"OK. Para la matrícula {row.student_id} cuenta el resultado {row.id} "
          f"({row.final_score:g}/{row.max_score:g}).")


def cmd_export(session, args) -> None:
    df = results_dataframe(session, args.exam_id)
    out = Path(args.output) if args.output else EXPORT_DIR / f"{args.exam_id}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")  # -sig: Excel muestra bien los acentos
    print(f"OK. {len(df)} filas exportadas a {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="Todos los resultados de un examen")
    p.add_argument("exam_id")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("duplicates", help="Matrículas escaneadas más de una vez sin elegir")
    p.add_argument("exam_id")
    p.set_defaults(func=cmd_duplicates)

    p = sub.add_parser("choose", help="Elegir qué resultado cuenta para su matrícula")
    p.add_argument("result_id")
    p.set_defaults(func=cmd_choose)

    p = sub.add_parser("export", help="Exportar a CSV")
    p.add_argument("exam_id")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    for subparser in sub.choices.values():
        add_verbose_flag(subparser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger.info("inicio: comando %s", args.command)
    session = open_session()
    try:
        args.func(session, args)
    except (repository.StorageError, ValueError) as exc:
        logger.error("falló '%s': %s", args.command, type(exc).__name__)
        print(f"ERROR: {exc}")
        raise SystemExit(1)
    logger.info("fin")


if __name__ == "__main__":
    main()
