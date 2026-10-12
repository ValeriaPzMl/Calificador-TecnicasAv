"""
Genera el diagrama de flujo del pipeline de calificación: uno general y uno
por cada función que aparece en él, todos en la misma imagen.

Uso:
    python scripts/pipeline_diagram.py

Guarda docs/pipeline_calificacion.pdf (vectorial, para hacer zoom) y
docs/pipeline_calificacion.png.

Los diagramas se describen a mano en CHARTS (abajo). Si cambia el código del
pipeline, hay que actualizarlos aquí.

Formas:
    pastilla verde      inicio (lo que recibe la función)
    pastilla gris       fin (lo que devuelve)
    rectángulo          paso dentro de la función
    rectángulo de color llamada a otra función; el color es su archivo y
                        "#n" es el número de su propio diagrama
    rombo amarillo      decisión
    hexágono morado     ciclo ("para cada..."); la flecha de la izquierda
                        regresa al inicio del ciclo
    caja roja           error (excepción)
    caja azul           return anticipado
"""

import sys
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs"
PNG_SCALE = 2.0

FONT_FILES = {
    "Sans": "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",
    "Sans-Bold": "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
    "Mono": "/usr/share/fonts/liberation-mono-fonts/LiberationMono-Regular.ttf",
    "Mono-Bold": "/usr/share/fonts/liberation-mono-fonts/LiberationMono-Bold.ttf",
}

# --- Medidas (pt) ---
CW = 390  # ancho de cada diagrama
MX = 152  # x de la columna principal
NW = 222  # ancho de los nodos principales
SIDE_X0 = MX + NW / 2 + 16  # inicio de las cajas laterales de las decisiones
SIDE_W = CW - SIDE_X0 - 10
GAP = 17  # espacio vertical (flecha) entre nodos
PAD = 5
FS = 7.6  # tamaño de letra
LEAD = 9.6
HEADER_H = 34
NCOL = 6
COL_GAP = 22
MARGIN = 30

# --- Colores por archivo: (encabezado, relleno de llamadas) ---
FILE_COLORS = {
    "python -m cli.grade": ("#263238", "#ECEFF1"),
    "cli/grade.py": ("#4A5D8A", "#E1E7F3"),
    "storage/db.py": ("#6E4C8F", "#ECE3F4"),
    "storage/repository.py": ("#8F4C74", "#F4E3EE"),
    "core/preprocessing.py": ("#3F7F68", "#DFF0E9"),
    "core/pipeline.py": ("#1F64B0", "#D9E8F8"),
    "core/alignment.py": ("#A8661F", "#F8E9D7"),
    "core/qr.py": ("#7C7A1F", "#F1F0D2"),
    "core/layout.py": ("#1F7C78", "#D6F0EE"),
    "models/exam.py": ("#5E5E5E", "#E9E9E9"),
    "core/detection.py": ("#B03535", "#F8DCDC"),
    "core/scoring.py": ("#2F8040", "#DAF0DD"),
}
INK = HexColor("#1E1E1E")
MUTED = HexColor("#5F6368")
LINE = HexColor("#424242")
DEC = (HexColor("#FFF6D1"), HexColor("#B8952A"))
LOOP = (HexColor("#EDE6F8"), HexColor("#5B3BA8"))
ERR = (HexColor("#FBE1DF"), HexColor("#C0392B"))
OK = (HexColor("#DFF3E2"), HexColor("#2E7D32"))
RET = (HexColor("#E3ECF7"), HexColor("#3E5F8A"))
START = (HexColor("#E6F4E7"), HexColor("#2E7D32"))
END = (HexColor("#EEEEEE"), HexColor("#616161"))
PROC = (HexColor("#FFFFFF"), HexColor("#616161"))


# --- Pasos de un diagrama ---

def S(text):  # inicio
    return ("start", text)


def E(text):  # fin
    return ("end", text)


def P(text):  # paso
    return ("proc", text)


def C(key, note=""):  # llamada a otra función (que tiene su propio diagrama)
    return ("call", key, note)


def D(question, side_label, side_text, kind, main_label, skip=0):
    """Decisión. La rama lateral (derecha) es `side_label`; la principal
    (hacia abajo) es `main_label`. kind: "err" (excepción, termina),
    "ret" (return, termina), "ok" (resultado, termina) o "join" (hace
    `side_text` y se reincorpora `skip` pasos más abajo)."""
    return ("dec", question, side_label, side_text, kind, main_label, skip)


def L(text):  # inicio de ciclo
    return ("loop", text)


def N(text):  # fin de una vuelta del ciclo (regresa al L más reciente)
    return ("next", text)


# --- Los diagramas ---
# (clave, nombre que se muestra, archivo, pasos)

CHARTS = [
    ("pipeline", "Pipeline completo", "python -m cli.grade", [
        S("python -m cli.grade data/raw/  (fotos de hojas contestadas)"),
        C("main", "lee argumentos y configura los logs"),
        C("open_session", "conecta a la BD (data/calificador.db)"),
        C("collect_photos", "lista de fotos a calificar"),
        L("Para cada foto"),
        C("grade_one", "califica una foto (todo lo de abajo)"),
        C("load_image", "archivo → imagen BGR"),
        C("grade_photo", "pasos 1-5"),
        C("detect_corner_markers", "1. centros de las 4 esquinas"),
        C("read_qr", "2. de qué examen es y tamaño de página"),
        C("get_exam", "3. el examen, desde la BD"),
        C("build_layout", "3. dónde va cada burbuja (mm)"),
        C("align_sheet", "4. hoja enderezada a tamaño fijo"),
        C("read_sheet", "4. matrícula + respuestas"),
        C("score", "5. nota por sección y final"),
        C("draw_reading", "imagen de revisión con colores"),
        C("add_result", "guardar el resultado en la BD"),
        C("set_image_path", "anotar dónde quedó la imagen"),
        N("siguiente foto"),
        E("Imprime matrícula y nota de cada hoja"),
    ]),
    ("main", "main()", "cli/grade.py", [
        S("python -m cli.grade <fotos o carpetas> [--exam] [--page] [--dry-run] [-v]"),
        P("argparse: leer los argumentos"),
        P("configurar los logs según -v / -vv"),
        C("open_session"),
        C("collect_photos"),
        L("Para cada foto"),
        C("grade_one", "True si se calificó, False si falló"),
        N("siguiente foto"),
        D("¿más de una foto?", "sí", "imprimir 'N de M hojas calificadas'", "join", "no"),
        D("¿falló alguna?", "sí", "salir con código 1", "err", "no"),
        E("Fin"),
    ]),
    ("open_session", "open_session()", "storage/db.py", [
        S("Recibe: url (opcional)"),
        C("make_engine"),
        C("init_db"),
        P("sessionmaker(engine)(): abrir una sesión"),
        E("Devuelve: Session"),
    ]),
    ("make_engine", "make_engine()", "storage/db.py", [
        S("Recibe: url (o None)"),
        D("¿se pasó url?", "no", "usar $DATABASE_URL o, si no hay, sqlite:///data/calificador.db",
          "join", "sí"),
        P("create_engine(url)  (SQLAlchemy)"),
        D("¿es SQLite?", "sí", "activar PRAGMA foreign_keys=ON al conectar (para ON DELETE CASCADE)",
          "join", "no"),
        E("Devuelve: Engine"),
    ]),
    ("init_db", "init_db()", "storage/db.py", [
        S("Recibe: Engine"),
        P("Base.metadata.create_all: crear las tablas que falten (exams, sections, questions, results)"),
        E("Sin retorno"),
    ]),
    ("collect_photos", "collect_photos()", "cli/grade.py", [
        S("Recibe: rutas de la línea de comandos"),
        L("Para cada ruta"),
        D("¿es carpeta?", "sí", "agregar sus .jpg / .jpeg / .png / .bmp / .webp, ordenados",
          "join", "no", skip=1),
        P("agregar la ruta tal cual"),
        N("siguiente ruta"),
        E("Devuelve: list[Path]"),
    ]),
    ("grade_one", "grade_one()", "cli/grade.py", [
        S("Recibe: session, foto, args"),
        C("load_image"),
        C("grade_photo", "find_exam = buscar el examen en la BD (get_exam)"),
        D("¿error? (archivo, marcadores o examen)", "sí", "imprimir ERROR y devolver False",
          "err", "no"),
        P("armar {pregunta: respuesta correcta} del examen"),
        C("draw_reading"),
        P("crear carpeta data/processed/results/<examen>/"),
        D("¿--dry-run?", "sí", "guardar <foto>_revision.png (no toca la BD)", "join", "no", skip=3),
        C("add_result", "devuelve la fila nueva y los duplicados"),
        P("guardar la imagen como <id del resultado>.png"),
        C("set_image_path"),
        P("imprimir matrícula, nota, secciones, ambiguas, en blanco y aviso de duplicado"),
        E("Devuelve: True"),
    ]),
    ("load_image", "load_image()", "core/preprocessing.py", [
        S("Recibe: ruta del archivo"),
        D("¿existe el archivo?", "no", "FileNotFoundError", "err", "sí"),
        P("cv2.imread(ruta)"),
        D("¿OpenCV la pudo leer?", "no", "FileNotFoundError", "err", "sí"),
        E("Devuelve: imagen BGR (alto × ancho × 3)"),
    ]),
    ("grade_photo", "grade_photo()", "core/pipeline.py", [
        S("Recibe: foto, find_exam, --exam y --page de respaldo"),
        C("detect_corner_markers", "paso 1"),
        C("read_qr", "paso 2"),
        D("¿no se leyó el QR y no hay --exam?", "sí", "GradingError: no se pudo leer el QR",
          "err", "no"),
        P("exam_id y page: los del QR (o --exam / --page si el QR no se leyó)"),
        C("get_exam", "paso 3, vía find_exam(exam_id)"),
        D("¿existe el examen?", "no", "GradingError: examen no registrado", "err", "sí"),
        C("build_layout", "paso 3, con el page del QR"),
        C("align_sheet", "paso 4, size = layout.canonical_size"),
        C("read_sheet", "paso 4"),
        C("score", "paso 5"),
        E("Devuelve: GradedSheet (examen, layout, hoja alineada, lectura, nota)"),
    ]),
    ("detect_corner_markers", "detect_corner_markers()", "core/alignment.py", [
        S("Recibe: foto BGR"),
        C("_detect_ids", "a resolución completa (escala 1)"),
        L("Para cada tamaño reducido: 2000, 1400, 1000 px"),
        D("¿ya están los 4?", "sí", "salir del ciclo", "join", "no", skip=4),
        P("reducir la foto (cv2.resize, INTER_AREA): el rayado se promedia"),
        C("_detect_ids", "en la foto reducida"),
        P("agregar solo los ids que faltaban"),
        N("siguiente tamaño"),
        D("¿se encontró alguno?", "no", "AlignmentError: ningún marcador", "err", "sí"),
        P("asignar cada id a su esquina: 0 sup-izq, 1 sup-der, 2 inf-der, 3 inf-izq"),
        D("¿falta alguna esquina?", "sí", "AlignmentError: faltan marcadores", "err", "no"),
        E("Devuelve: DetectedMarkers {esquina: (x, y) px}"),
    ]),
    ("_detect_ids", "_detect_ids()", "core/alignment.py", [
        S("Recibe: imagen, escala"),
        P("ArucoDetector.detectMarkers (diccionario DICT_4X4_50)"),
        D("¿encontró alguno?", "no", "Devuelve {}", "ret", "sí"),
        L("Para cada marcador"),
        P("centro = promedio de sus 4 esquinas ÷ escala (px de la foto original)"),
        N("siguiente marcador"),
        E("Devuelve: {id: (x, y)}"),
    ]),
    ("read_qr", "read_qr()", "core/qr.py", [
        S("Recibe: foto, marcadores"),
        L("Para cada página: carta, A4"),
        C("page_geometry", "dónde está el QR en mm"),
        P("región = cuadro del QR + 3 mm de margen"),
        C("warp_region", "endereza solo esa región, a 8 px/mm"),
        C("to_grayscale"),
        C("_decode"),
        D("¿empieza con 'EG1:'?", "sí", "Devuelve QrReading(exam_id, page)", "ret", "no"),
        N("siguiente página"),
        E("Devuelve: None (no se pudo leer)"),
    ]),
    ("page_geometry", "page_geometry()", "core/layout.py", [
        S("Recibe: page ('letter' o 'a4')"),
        D("¿página conocida?", "no", "ValueError", "err", "sí"),
        P("tamaño de la hoja en mm (carta: 215.9 × 279.4)"),
        P("centros de los marcadores: margen 10 mm + medio marcador (16 mm)"),
        P("zona de contenido: entre los centros de los marcadores"),
        P("cuadro del QR: arriba a la derecha, 24 × 24 mm"),
        E("Devuelve: PageGeometry (igual para todos los exámenes)"),
    ]),
    ("warp_region", "warp_region()", "core/alignment.py", [
        S("Recibe: foto, marcadores, centros en mm, región (x, y, ancho, alto) en mm, px/mm"),
        P("origen: centros de los marcadores en la foto (px)"),
        P("destino: centros en mm − esquina de la región, × px/mm"),
        P("cv2.getPerspectiveTransform(origen, destino)"),
        P("cv2.warpPerspective al tamaño de la región"),
        E("Devuelve: recorte enderezado"),
    ]),
    ("to_grayscale", "to_grayscale()", "core/preprocessing.py", [
        S("Recibe: imagen"),
        D("¿ya está en gris (2 dimensiones)?", "sí", "Devuelve la misma imagen", "ret", "no"),
        P("cv2.cvtColor BGR → gris"),
        E("Devuelve: imagen en gris"),
    ]),
    ("_decode", "_decode()", "core/qr.py", [
        S("Recibe: recorte en gris"),
        P("QRCodeDetector.detectAndDecode"),
        D("¿leyó algo?", "sí", "Devuelve el texto", "ret", "no"),
        P("blur gaussiano 5×5 + umbral de Otsu (blanco y negro puro)"),
        P("detectAndDecode otra vez"),
        E("Devuelve: texto ('' si no se leyó)"),
    ]),
    ("get_exam", "get_exam()", "storage/repository.py", [
        S("Recibe: session, exam_id, owner_id (opcional)"),
        C("_exam_row"),
        D("¿se encontró?", "no", "Devuelve None", "ret", "sí"),
        C("_row_to_exam"),
        E("Devuelve: Exam"),
    ]),
    ("_exam_row", "_exam_row()", "storage/repository.py", [
        S("Recibe: session, exam_id, owner_id"),
        P("session.get(ExamRow, exam_id): SELECT por llave primaria"),
        D("¿existe y es de otro usuario?", "sí", "Devuelve None", "ret", "no"),
        E("Devuelve: ExamRow (o None si no existe)"),
    ]),
    ("_row_to_exam", "_row_to_exam()", "storage/repository.py", [
        S("Recibe: ExamRow (fila de la tabla exams)"),
        L("Para cada sección (ordenadas por position)"),
        P("Section(key, name, weight) con sus Question(number, correct_answer, points)"),
        N("siguiente sección"),
        P("Exam(id, name = title, num_options, student_id_digits, sections)"),
        E("Devuelve: Exam"),
    ]),
    ("build_layout", "build_layout()", "core/layout.py", [
        S("Recibe: Exam, page"),
        C("validate"),
        C("page_geometry"),
        P("encabezado: nombre del examen y resumen"),
        L("Para cada dígito de la matrícula"),
        P("cuadro para escribirlo + 10 burbujas (0-9) en una fila"),
        N("siguiente dígito"),
        P("QR (posición fija) y bloque Nombre / Grupo / Fecha / Instrucciones"),
        P("cuántas columnas de preguntas caben y cuántas filas por columna"),
        P("filas = [encabezado de sección, sus preguntas...] por sección"),
        L("Para cada altura de columna, de la mínima a la máxima"),
        C("_flow_rows", "intenta acomodar las filas"),
        D("¿cupo todo?", "sí", "salir del ciclo", "join", "no", skip=1),
        N("siguiente altura"),
        D("¿se encontró acomodo?", "no", "LayoutError: no cabe en una hoja", "err", "sí"),
        L("Para cada fila acomodada"),
        P("sección → texto 'Nombre (40%)'; pregunta → número + burbujas A, B, C..."),
        N("siguiente fila"),
        E("Devuelve: SheetLayout (todo en mm)"),
    ]),
    ("validate", "Exam.validate()", "models/exam.py", [
        S("Recibe: el examen (self)"),
        P("revisar: 2 ≤ opciones ≤ 8, 1 ≤ dígitos ≤ 12, hay secciones"),
        L("Para cada sección y cada pregunta"),
        P("revisar: sección no vacía, número no repetido, respuesta dentro de las opciones, puntos > 0"),
        N("siguiente"),
        P("revisar pesos: todos o ninguno, y que sumen 100"),
        D("¿hubo problemas?", "sí", "ExamValidationError con la lista completa", "err", "no"),
        E("Sin retorno (el examen es válido)"),
    ]),
    ("_flow_rows", "_flow_rows()", "core/layout.py", [
        S("Recibe: filas, altura de columna, número de columnas"),
        P("col = 0, fila = 0"),
        L("Para cada fila (encabezado o pregunta)"),
        D("¿columna llena, o encabezado que quedaría solo al final?", "sí",
          "pasar a la siguiente columna (col + 1, fila = 0)", "join", "no"),
        D("¿col ≥ número de columnas?", "sí", "Devuelve None (no cabe)", "ret", "no"),
        P("guardar (col, fila); fila + 1"),
        N("siguiente fila"),
        E("Devuelve: lista de (col, fila)"),
    ]),
    ("align_sheet", "align_sheet()", "core/alignment.py", [
        S("Recibe: foto, marcadores, size (ancho, alto) en px"),
        D("¿se pasaron marcadores?", "no", "detect_corner_markers(foto)  (#{detect_corner_markers})",
          "join", "sí"),
        P("origen: centros de los 4 marcadores en la foto"),
        P("destino: las 4 esquinas de una imagen de tamaño size"),
        P("cv2.getPerspectiveTransform → matriz 3×3"),
        P("cv2.warpPerspective(foto, matriz, size)"),
        E("Devuelve: hoja enderezada (p. ej. 900 × 1217 px, 5 px/mm)"),
    ]),
    ("read_sheet", "read_sheet()", "core/detection.py", [
        S("Recibe: hoja alineada, layout"),
        D("¿mide layout.canonical_size?", "no", "ValueError", "err", "sí"),
        C("to_grayscale"),
        L("Paso 1: para cada burbuja (matrícula y preguntas)"),
        C("bubble_fill"),
        N("siguiente burbuja"),
        C("calibrate", "paso 2: con todas las filas de la hoja"),
        L("Paso 3: para cada dígito de matrícula"),
        C("classify"),
        P("dígito = el valor si es 'answered', si no '?'"),
        N("siguiente dígito"),
        P("student_id = None si tiene algún '?'"),
        L("Para cada pregunta"),
        C("classify"),
        P("guardar la respuesta; si es ambigua o en blanco, anotarla en su lista"),
        N("siguiente pregunta"),
        P("confianza = el menor margen entre las contestadas"),
        E("Devuelve: SheetReading"),
    ]),
    ("bubble_fill", "bubble_fill()", "core/detection.py", [
        S("Recibe: hoja en gris, layout, burbuja"),
        C("bubble_px", "centro y radio en px"),
        P("papel = percentil 90 de un cuadro de ±2.2 radios alrededor"),
        P("centro = gris promedio de un círculo de 0.65 radios"),
        P("fill = 1 − centro / papel, limitado a [0, 1]"),
        E("Devuelve: fill (0 = papel, 1 = negro)"),
    ]),
    ("bubble_px", "SheetLayout.bubble_px()", "core/layout.py", [
        S("Recibe: burbuja (cx, cy, r en mm)"),
        C("to_px", "centro mm → px"),
        P("radio en px = r × 5 px/mm"),
        E("Devuelve: (cx, cy, r) en px"),
    ]),
    ("to_px", "SheetLayout.to_px()", "core/layout.py", [
        S("Recibe: (x, y) en mm de la página"),
        P("restar el centro del marcador sup-izq (es el origen de la hoja alineada)"),
        P("escalar por (ancho px − 1) / ancho mm entre marcadores (igual en y)"),
        E("Devuelve: (x, y) en px de la hoja alineada"),
    ]),
    ("calibrate", "calibrate()", "core/detection.py", [
        S("Recibe: fills de todas las filas (dígitos y preguntas)"),
        P("vacía = percentil 25 de todos los fills (la mayoría están vacías)"),
        P("filas con marca = las que su máximo supera a 'vacía' por ≥ 0.15"),
        D("¿hay filas con marca?", "no", "Devuelve Calibration(vacía, None): hoja en blanco",
          "ret", "sí"),
        P("marca típica = mediana del máximo de esas filas"),
        E("Devuelve: Calibration(vacía, marca)"),
    ]),
    ("classify", "classify()", "core/detection.py", [
        S("Recibe: fills de una fila, valores (A, B... o 0-9), calibración"),
        L("Para cada fill"),
        C("normalize", "0 = vacía, 1 = marca típica"),
        N("siguiente"),
        P("ordenar; margen = mejor − segunda"),
        P("rellenas = cuántas ≥ 0.55; dudosas = cuántas entre 0.25 y 0.55"),
        D("¿exactamente 1 rellena y margen ≥ 0.35?", "sí", "Devuelve ('answered', valor)", "ret",
          "no"),
        D("¿0 rellenas y 0 dudosas?", "sí", "Devuelve ('blank', None)", "ret", "no"),
        E("Devuelve ('ambiguous', None): nunca se adivina"),
    ]),
    ("normalize", "Calibration.normalize()", "core/detection.py", [
        S("Recibe: fill"),
        D("¿hay marca típica?", "no", "Devuelve 0", "ret", "sí"),
        P("(fill − vacía) / (marca − vacía)"),
        E("Devuelve: 0 = como vacía, 1 = como marca típica"),
    ]),
    ("score", "score()", "core/scoring.py", [
        S("Recibe: Exam, SheetReading"),
        L("Para cada sección"),
        L("Para cada pregunta de la sección"),
        D("¿respuesta leída = correcta?", "sí", "sumar sus puntos y anotarla como correcta",
          "join", "no"),
        N("siguiente pregunta"),
        P("SectionScore: puntos, máximo, porcentaje"),
        N("siguiente sección"),
        D("¿todas las secciones tienen peso?", "sí", "final = Σ porcentaje × peso / 100 (sobre 100)",
          "join", "no", skip=1),
        P("final = suma de puntos (sobre el total de puntos)"),
        P("Result con la nota redondeada a 2 decimales"),
        E("Devuelve: Result"),
    ]),
    ("draw_reading", "draw_reading()", "core/detection.py", [
        S("Recibe: hoja alineada, layout, lectura, respuestas correctas"),
        P("copiar la hoja, a color"),
        L("Para cada dígito de matrícula"),
        D("¿dígito ilegible ('?')?", "sí", "círculo naranja en sus 10 burbujas", "join", "no",
          skip=1),
        P("círculo verde en la burbuja leída"),
        N("siguiente dígito"),
        L("Para cada pregunta y cada burbuja"),
        P("ambigua → naranja · en blanco → gris · leída → verde si es correcta, rojo si no · "
          "correcta no marcada → verde delgado"),
        N("siguiente"),
        E("Devuelve: imagen de revisión"),
    ]),
    ("add_result", "add_result()", "storage/repository.py", [
        S("Recibe: session, Result, SheetReading"),
        D("¿existe el examen en la BD?", "no", "ExamNotFoundError", "err", "sí"),
        P("otros = resultados de esa matrícula en ese examen (ninguno si no se leyó la matrícula)"),
        D("¿hay otros?", "sí", "marcarlos selected = False: el profesor elegirá cuál cuenta",
          "join", "no"),
        P("nueva ResultRow con selected = (no hay otros)"),
        C("_fill_result_row"),
        P("session.add + commit"),
        E("Devuelve: (fila nueva, otros)"),
    ]),
    ("_fill_result_row", "_fill_result_row()", "storage/repository.py", [
        S("Recibe: fila, Result, SheetReading"),
        P("copiar matrícula, nota y nota máxima"),
        P("section_scores → lista JSON (puntos, máximo, peso, %)"),
        P("answers y fills → JSON (llaves como texto)"),
        P("copiar ambiguas, en blanco, confianza y needs_review"),
        E("Sin retorno (modifica la fila)"),
    ]),
    ("set_image_path", "set_image_path()", "storage/repository.py", [
        S("Recibe: session, id del resultado, ruta de la imagen"),
        P("session.get(ResultRow, id)"),
        D("¿existe?", "no", "ResultNotFoundError", "err", "sí"),
        P("fila.image_path = ruta; commit"),
        E("Sin retorno"),
    ]),
]

NUMBERS = {key: i for i, (key, *_rest) in enumerate(CHARTS)}
BY_KEY = {key: (title, file) for key, title, file, _ in CHARTS}


# --- Dibujo ---
# Cada diagrama se arma como una lista de operaciones en coordenadas
# locales (y hacia abajo) y luego se dibuja en su lugar de la página.

def wrap(text, font, size, width):
    return simpleSplit(text, font, size, width)


def fill_refs(text):
    """Reemplaza {clave} por el número de su diagrama."""
    for key, n in NUMBERS.items():
        text = text.replace("{%s}" % key, str(n))
    return text


class Chart:
    def __init__(self, key, title, file, steps, badge=None, colors=None):
        """`badge` y `colors` (encabezado, relleno) permiten diagramas que no
        son una función de CHARTS (ver scripts/detection_diagram.py)."""
        self.key, self.title, self.file, self.steps = key, title, file, steps
        self.badge = badge or f"#{NUMBERS[key]}"
        self.colors = colors or FILE_COLORS[file]
        self.ops: list[tuple] = []
        self.height = 0.0
        self._layout()

    # -- primitivas --
    def op(self, *args):
        self.ops.append(args)

    def arrow(self, pts, label=None, label_pos=None, align="left"):
        self.op("arrow", pts)
        if label:
            x, y = label_pos
            self.op("text", x, y, label, "Sans-Bold", 6.8, MUTED, align)

    def text_block(self, lines, x, y_top, align="center", font="Sans", size=FS, color=INK):
        for i, line in enumerate(lines):
            self.op("text", x, y_top + (i + 1) * LEAD - 2.4, line, font, size, color, align)

    # -- nodos --
    def box(self, kind, y, text, fill, stroke, width=NW, x=None, round_=0):
        x = MX - width / 2 if x is None else x
        lines = wrap(text, "Sans", FS, width - 2 * PAD - (8 if round_ else 0))
        h = 2 * PAD + len(lines) * LEAD
        self.op("rect", x, y, width, h, fill, stroke, round_ and h / 2)
        self.text_block(lines, x + width / 2, y + PAD)
        return h

    def call_node(self, y, key, note):
        title, file = BY_KEY[key]
        header, light = FILE_COLORS[file]
        name_lines = [title]
        sub = [file]
        note_lines = wrap(note, "Sans", FS - 0.4, NW - 2 * PAD - 12) if note else []
        h = 2 * PAD + LEAD + 8.5 + len(note_lines) * (LEAD - 0.6)
        x = MX - NW / 2
        self.op("rect", x, y, NW, h, HexColor(light), HexColor(header), 0)
        # barras laterales: "proceso predefinido" (tiene su propio diagrama)
        self.op("line", [(x + 5, y), (x + 5, y + h)], HexColor(header))
        self.op("line", [(x + NW - 5, y), (x + NW - 5, y + h)], HexColor(header))
        self.op("text", x + 11, y + PAD + LEAD - 2, name_lines[0], "Mono-Bold", FS + 0.4,
                HexColor(header), "left")
        self.op("text", x + NW - 10, y + PAD + LEAD - 2, f"→ #{NUMBERS[key]}", "Sans-Bold",
                FS + 0.4, HexColor(header), "right")
        self.op("text", x + 11, y + PAD + LEAD + 7.2, sub[0], "Sans", 6.2, MUTED, "left")
        for i, line in enumerate(note_lines):
            self.op("text", x + 11, y + PAD + LEAD + 8.5 + (i + 1) * (LEAD - 0.6) - 2,
                    line, "Sans", FS - 0.4, INK, "left")
        return h

    def diamond(self, y, question):
        lines = wrap(question, "Sans", FS, NW * 0.6)
        h = max(38, len(lines) * LEAD + 24)
        cx, cy = MX, y + h / 2
        pts = [(cx, y), (cx + NW / 2, cy), (cx, y + h), (cx - NW / 2, cy)]
        self.op("poly", pts, *DEC)
        self.text_block(lines, cx, cy - len(lines) * LEAD / 2)
        return h

    def hexagon(self, y, text):
        lines = wrap(text, "Sans-Bold", FS, NW - 30)
        h = 2 * PAD + len(lines) * LEAD + 2
        x0, x1, k = MX - NW / 2, MX + NW / 2, 10
        pts = [(x0 + k, y), (x1 - k, y), (x1, y + h / 2), (x1 - k, y + h), (x0 + k, y + h),
               (x0, y + h / 2)]
        self.op("poly", pts, *LOOP)
        self.text_block(lines, MX, y + PAD + 1, font="Sans-Bold", color=LOOP[1])
        return h

    # -- armado --
    def _layout(self):
        y = HEADER_H + 14
        prev_bottom = None
        next_label = None  # etiqueta de la flecha que entra al siguiente nodo
        pending_joins: dict[int, list[tuple[float, float]]] = {}
        loops: list[tuple[float, float]] = []  # (y centro, x izquierda) del hexágono

        for i, step in enumerate(self.steps):
            kind = step[0]
            top = y if prev_bottom is None else prev_bottom + GAP

            # ramas laterales que se reincorporan aquí
            for sx, sy in pending_joins.pop(i, []):
                jy = top - 6
                self.op("line", [(sx, sy), (sx, jy)], LINE)
                self.op("arrow", [(sx, jy), (MX + 1, jy)])

            if prev_bottom is not None:
                # a la izquierda: por la derecha llegan las ramas que se reincorporan
                self.arrow([(MX, prev_bottom), (MX, top)], next_label,
                           (MX - 5, prev_bottom + 9), align="right")
            next_label = None

            if kind == "start":
                h = self.box(kind, top, step[1], *START, round_=1)
            elif kind == "end":
                h = self.box(kind, top, step[1], *END, round_=1)
            elif kind == "proc":
                h = self.box(kind, top, step[1], *PROC)
            elif kind == "call":
                h = self.call_node(top, step[1], step[2])
            elif kind == "loop":
                h = self.hexagon(top, step[1])
                loops.append((top + h / 2, MX - NW / 2))
            elif kind == "next":
                h = self.box(kind, top, step[1], LOOP[0], LOOP[1], width=104, round_=1)
                hy, hx = loops.pop()
                lane = MX - NW / 2 - 12 - 8 * len(loops)
                ny = top + h / 2
                self.op("line", [(MX - 52, ny), (lane, ny), (lane, hy)], LOOP[1])
                self.op("arrow", [(lane, hy), (hx, hy)], LOOP[1])
                next_label = "al terminar"
            elif kind == "dec":
                _, question, side_label, side_text, side_kind, main_label, skip = step
                h = self.diamond(top, question)
                cy = top + h / 2
                fill, stroke = {"err": ERR, "ret": RET, "ok": OK, "join": PROC}[side_kind]
                side_lines = wrap(fill_refs(side_text), "Sans", FS, SIDE_W - 2 * PAD)
                sh = 2 * PAD + len(side_lines) * LEAD
                sy = cy - sh / 2
                self.op("rect", SIDE_X0, sy, SIDE_W, sh, fill, stroke,
                        6 if side_kind != "join" else 0)
                self.text_block(side_lines, SIDE_X0 + SIDE_W / 2, sy + PAD,
                                color=stroke if side_kind != "join" else INK)
                self.arrow([(MX + NW / 2, cy), (SIDE_X0, cy)], side_label,
                           (MX + NW / 2 + 2, cy - 3))
                if side_kind == "join":
                    target = i + 1 + skip
                    pending_joins.setdefault(target, []).append(
                        (SIDE_X0 + SIDE_W / 2, sy + sh))
                next_label = main_label
                h = max(h, sy + sh - top)  # que lo siguiente quede debajo de la caja lateral
            else:
                raise ValueError(kind)

            prev_bottom = top + h

        self.height = prev_bottom + 14

    # -- dibujo en la página --
    def draw(self, c: Canvas, ox: float, top: float):
        def Y(y):
            return top - y

        header, light = self.colors
        is_overview = self.key == "pipeline"
        # marco y encabezado
        c.setLineWidth(1.6 if is_overview else 0.9)
        c.setStrokeColor(HexColor(header))
        c.setFillColor(HexColor("#FFFFFF"))
        c.roundRect(ox, Y(self.height), CW, self.height, 8, stroke=1, fill=1)
        c.setFillColor(HexColor(header))
        c.roundRect(ox, Y(HEADER_H), CW, HEADER_H, 8, stroke=0, fill=1)
        c.rect(ox, Y(HEADER_H), CW, 10, stroke=0, fill=1)
        c.setFillColor(HexColor("#FFFFFF"))
        c.circle(ox + 20, Y(HEADER_H / 2), 12, stroke=0, fill=1)
        c.setFillColor(HexColor(header))
        c.setFont("Sans-Bold", 10)
        c.drawCentredString(ox + 20, Y(HEADER_H / 2) - 3.5, self.badge)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Mono-Bold" if self.title.endswith(")") else "Sans-Bold", 11)
        c.drawString(ox + 40, Y(15), self.title)
        c.setFont("Sans", 7.5)
        c.drawString(ox + 40, Y(27), self.file)

        for op in self.ops:
            kind = op[0]
            if kind == "rect":
                _, x, y, w, h, fill, stroke, radius = op
                c.setFillColor(fill)
                c.setStrokeColor(stroke)
                c.setLineWidth(0.8)
                if radius:
                    c.roundRect(ox + x, Y(y + h), w, h, radius, stroke=1, fill=1)
                else:
                    c.rect(ox + x, Y(y + h), w, h, stroke=1, fill=1)
            elif kind == "poly":
                _, pts, fill, stroke = op
                path = c.beginPath()
                path.moveTo(ox + pts[0][0], Y(pts[0][1]))
                for px, py in pts[1:]:
                    path.lineTo(ox + px, Y(py))
                path.close()
                c.setFillColor(fill)
                c.setStrokeColor(stroke)
                c.setLineWidth(0.8)
                c.drawPath(path, stroke=1, fill=1)
            elif kind in ("line", "arrow"):
                pts = op[1]
                color = op[2] if len(op) > 2 else LINE
                c.setStrokeColor(color)
                c.setLineWidth(0.8)
                path = c.beginPath()
                path.moveTo(ox + pts[0][0], Y(pts[0][1]))
                for px, py in pts[1:]:
                    path.lineTo(ox + px, Y(py))
                c.drawPath(path, stroke=1, fill=0)
                if kind == "arrow":
                    (x0, y0), (x1, y1) = pts[-2], pts[-1]
                    _arrow_head(c, ox + x0, Y(y0), ox + x1, Y(y1), color)
            elif kind == "text":
                _, x, y, text, font, size, color, align = op
                c.setFont(font, size)
                c.setFillColor(color)
                draw = {"left": c.drawString, "center": c.drawCentredString,
                        "right": c.drawRightString}[align]
                draw(ox + x, Y(y), text)


def _arrow_head(c, x0, y0, x1, y1, color, size=4.2):
    dx, dy = x1 - x0, y1 - y0
    length = (dx * dx + dy * dy) ** 0.5 or 1
    ux, uy = dx / length, dy / length
    path = c.beginPath()
    path.moveTo(x1, y1)
    path.lineTo(x1 - ux * size - uy * size * 0.55, y1 - uy * size + ux * size * 0.55)
    path.lineTo(x1 - ux * size + uy * size * 0.55, y1 - uy * size - ux * size * 0.55)
    path.close()
    c.setFillColor(color)
    c.drawPath(path, stroke=0, fill=1)


def draw_legend(c: Canvas, x: float, y: float, width: float) -> float:
    """Título, leyenda de formas y colores por archivo. Devuelve la altura usada."""
    c.setFillColor(INK)
    c.setFont("Sans-Bold", 22)
    c.drawString(x, y - 22, "Calificador — pipeline de calificación de un examen")
    c.setFont("Sans", 10)
    c.setFillColor(MUTED)
    c.drawString(x, y - 40, "python -m cli.grade <fotos>  ·  #0 es el flujo completo; cada "
                 "rectángulo de color es una llamada a otra función y '→ #n' es el número "
                 "de su diagrama.  Generado por scripts/pipeline_diagram.py")

    sy = y - 62
    draw_shapes_legend(c, x, sy)
    return _draw_files_legend(c, x, y, sy)


def draw_shapes_legend(c: Canvas, x: float, sy: float, extra=()) -> None:
    """Una fila con las formas y su significado, centrada en `sy`."""
    shapes = [("Inicio: lo que recibe", START, 9), ("Fin: lo que devuelve", END, 9),
              ("Paso", PROC, 0), ("Decisión", DEC, -1), ("Ciclo", LOOP, -2),
              ("Error (excepción)", ERR, 4), ("Return anticipado", RET, 4), *extra]
    sx = x
    for label, (fill, stroke), kind in shapes:
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(0.8)
        if kind == -1:
            p = c.beginPath()
            p.moveTo(sx + 14, sy + 8); p.lineTo(sx + 28, sy); p.lineTo(sx + 14, sy - 8)
            p.lineTo(sx, sy); p.close()
            c.drawPath(p, stroke=1, fill=1)
        elif kind == -2:
            p = c.beginPath()
            p.moveTo(sx + 5, sy + 7); p.lineTo(sx + 23, sy + 7); p.lineTo(sx + 28, sy)
            p.lineTo(sx + 23, sy - 7); p.lineTo(sx + 5, sy - 7); p.lineTo(sx, sy); p.close()
            c.drawPath(p, stroke=1, fill=1)
        else:
            c.roundRect(sx, sy - 7, 28, 14, kind, stroke=1, fill=1)
        c.setFillColor(INK)
        c.setFont("Sans", 9)
        c.drawString(sx + 34, sy - 3, label)
        sx += 34 + stringWidth(label, "Sans", 9) + 22


def _draw_files_legend(c: Canvas, x: float, y: float, sy: float) -> float:
    fy = sy - 26
    fx = x
    c.setFont("Sans-Bold", 9)
    c.setFillColor(INK)
    c.drawString(fx, fy - 3, "Archivos:")
    fx += 52
    for file, (header, light) in FILE_COLORS.items():
        if file == "python -m cli.grade":
            continue
        c.setFillColor(HexColor(light))
        c.setStrokeColor(HexColor(header))
        c.rect(fx, fy - 7, 28, 14, stroke=1, fill=1)
        c.setStrokeColor(HexColor(header))
        c.line(fx + 4, fy - 7, fx + 4, fy + 7)
        c.line(fx + 24, fy - 7, fx + 24, fy + 7)
        c.setFillColor(HexColor(header))
        c.setFont("Mono-Bold", 9)
        c.drawString(fx + 34, fy - 3, file)
        fx += 34 + stringWidth(file, "Mono-Bold", 9) + 20
    return y - fy + 22


def register_fonts() -> None:
    for name, path in FONT_FILES.items():
        if not Path(path).exists():
            sys.exit(f"Falta la fuente {path}")
        pdfmetrics.registerFont(TTFont(name, path))


def main() -> None:
    register_fonts()

    charts = [Chart(*spec) for spec in CHARTS]
    for chart in charts:
        for step in chart.steps:
            if step[0] == "call" and step[1] not in NUMBERS:
                sys.exit(f"{chart.key}: llama a '{step[1]}', que no tiene diagrama")

    page_w = 2 * MARGIN + NCOL * CW + (NCOL - 1) * COL_GAP
    legend_h = 120

    # Acomodo por columnas: cada diagrama va a la columna más corta, en orden
    col_heights = [0.0] * NCOL
    placements = []
    for chart in charts:
        col = min(range(NCOL), key=lambda k: col_heights[k])
        placements.append((chart, col, col_heights[col]))
        col_heights[col] += chart.height + COL_GAP
    page_h = MARGIN + legend_h + max(col_heights) + MARGIN

    OUT_DIR.mkdir(exist_ok=True)
    pdf_path = OUT_DIR / "pipeline_calificacion.pdf"
    c = Canvas(str(pdf_path), pagesize=(page_w, page_h))
    c.setTitle("Pipeline de calificación")
    c.setFillColor(HexColor("#FAFAF7"))
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    draw_legend(c, MARGIN, page_h - MARGIN, page_w - 2 * MARGIN)
    for chart, col, y_off in placements:
        ox = MARGIN + col * (CW + COL_GAP)
        chart.draw(c, ox, page_h - MARGIN - legend_h - y_off)
    c.showPage()
    c.save()

    png_path = pdf_path.with_suffix(".png")
    pdfium.PdfDocument(str(pdf_path))[0].render(scale=PNG_SCALE).to_pil().save(png_path)
    print(f"OK. {len(charts)} diagramas")
    print(f"    PDF: {pdf_path}")
    print(f"    PNG: {png_path}  ({round(page_w * PNG_SCALE)} × {round(page_h * PNG_SCALE)} px)")


if __name__ == "__main__":
    main()
