// /restablecer: a donde regresa el enlace del correo de recuperación.
// supabase-js ya abrió la sesión con el enlace; aquí se pone la contraseña
// nueva. Sin sesión (enlace vencido o abierto en otro navegador) pide otro.
import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth.jsx";
import { log } from "../lib/log.js";
import { MIN_PASSWORD } from "./Login.jsx";

export default function ResetPassword() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (auth.local) return <Navigate to="/examenes" replace />;

  if (!auth.session) {
    log.info("restablecer sin sesión:", auth.linkError || "sin enlace");
    return (
      <main>
        <h1>Calificador</h1>
        <div className="card">
          <h2>Enlace no válido</h2>
          <p>{auth.linkError || "El enlace ya venció, ya se usó o se abrió en otro navegador."}</p>
          <Link className="btn" to="/login?olvide">Pedir otro enlace</Link>
        </div>
      </main>
    );
  }

  async function submit(ev) {
    ev.preventDefault();
    setError("");
    if (password.length < MIN_PASSWORD)
      return setError(`La contraseña debe tener al menos ${MIN_PASSWORD} caracteres.`);
    if (password !== password2) return setError("Las contraseñas no coinciden.");
    setBusy(true);
    try {
      await auth.updatePassword(password);
      navigate("/examenes", { replace: true, state: { notice: "Listo, tu contraseña cambió." } });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Calificador</h1>
      <form className="card" onSubmit={submit}>
        <h2>Contraseña nueva</h2>
        <p className="note">Para {auth.email}</p>
        <input type="email" autoComplete="username" value={auth.email ?? ""} readOnly hidden />
        <label htmlFor="new-password">Contraseña nueva</label>
        <input id="new-password" type="password" autoComplete="new-password" required
               value={password} onChange={(e) => setPassword(e.target.value)} />
        <label htmlFor="new-password2">Repítela</label>
        <input id="new-password2" type="password" autoComplete="new-password" required
               value={password2} onChange={(e) => setPassword2(e.target.value)} />
        <p className="note">Mínimo {MIN_PASSWORD} caracteres.</p>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>{busy ? "Un momento…" : "Guardar contraseña"}</button>
        <Link to="/examenes" className="note">Cancelar</Link>
      </form>
    </main>
  );
}
