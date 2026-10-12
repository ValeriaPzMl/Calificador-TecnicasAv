"""
Configura los logs de los comandos de cli/.

Cada módulo de core/ y models/ escribe con `logging.getLogger(__name__)`,
pero no decide qué se muestra: eso se decide aquí, según cuántas `-v` se
le pasen al comando:

    (nada)  solo avisos y errores
    -v      el flujo: qué función se llama y con qué resultado (INFO)
    -vv     además, el detalle de cada elemento: cada burbuja, texto,
            marcador y conversión mm -> px (DEBUG)

Los logs salen por stderr, así no se mezclan con los mensajes normales.
"""

import logging

LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s.%(funcName)s() | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def add_verbose_flag(parser) -> None:
    parser.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Mostrar logs del flujo (-v) o con todo el detalle (-vv)",
    )


def setup_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=DATE_FORMAT)
    # Solo nuestros módulos; las librerías (reportlab, PIL) se quedan calladas
    for noisy in ("PIL", "reportlab", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
