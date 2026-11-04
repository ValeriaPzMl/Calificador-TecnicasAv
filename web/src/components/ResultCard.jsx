// Nota de una hoja calificada (respuesta de /api/grade o del escaneo en vivo).
const fmt = (n) => (Math.round(n * 100) / 100).toString();

export default function ResultCard({ result: r, onAgain, onExit }) {
  return (
    <>
      <div className="card">
        <div style={{ fontWeight: 700 }}>{r.exam.name}</div>
        <div className="note">
          Examen {r.exam.id}{r.qr_read ? "" : " (QR no leído: se usó el examen elegido)"}
        </div>
        <div className="k">Matrícula</div>
        <p className={`sid${r.student_id ? "" : " bad"}`}>
          {r.student_id_raw}{r.student_id ? "" : "  ← incompleta"}
        </p>
        <div className="k">Nota</div>
        <p className="score">{fmt(r.final_score)} / {fmt(r.max_score)}</p>
        <ul className="sections">
          {r.sections.map((s) => (
            <li key={s.name}>
              {s.name}: {fmt(s.points)}/{fmt(s.max_points)} ({Math.round(s.percent)}%)
              {s.weight != null ? ` · vale ${fmt(s.weight)}%` : ""}
            </li>
          ))}
        </ul>
        {r.ambiguous.length > 0 && <div className="warn">Ambiguas (valen 0, revisar): {r.ambiguous.join(", ")}</div>}
        {r.blank.length > 0 && <div className="note">En blanco: {r.blank.join(", ")}</div>}
        {r.duplicates > 0 && (
          <div className="warn">Esta matrícula ya tenía {r.duplicates} resultado(s): elige cuál cuenta con cli.results.</div>
        )}
        <div className="note" style={{ marginTop: 8 }}>{r.saved ? "Guardado." : "Sin guardar (solo prueba)."}</div>
        <button onClick={onAgain}>Calificar otro</button>
        <button className="secondary" onClick={onExit}>Salir</button>
      </div>
      {r.review_image && (
        <div className="card">
          <div className="k" style={{ marginTop: 0 }}>
            Revisión: verde correcta · rojo incorrecta · naranja ambigua · gris en blanco
          </div>
          <img className="review" src={r.review_image} alt="Imagen de revisión" />
        </div>
      )}
    </>
  );
}
