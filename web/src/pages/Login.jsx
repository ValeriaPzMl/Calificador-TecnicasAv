// Iniciar sesión o crear cuenta (Supabase). Sin confirmar el correo: al
// crear la cuenta se entra directo. Si en Supabase se activa "Confirm
// email", avisa que revise su correo.
import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth.jsx";

const MIN_PASSWORD = 6; // el mínimo por defecto de Supabase

export default function Login() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  if (auth.local || auth.session) return <Navigate to="/examenes" replace />;
  const signup = tab === "signup";

  async function submit(ev) {
    ev.preventDefault();
    setError(""); setInfo("");
    if (signup && password.length < MIN_PASSWORD)
      return setError(`La contraseña debe tener al menos ${MIN_PASSWORD} caracteres.`);
    if (signup && password !== password2) return setError("Las contraseñas no coinciden.");
    setBusy(true);
    try {
      if (signup) {
        const entered = await auth.signUp(email.trim(), password);
        if (!entered) {
          setInfo("Cuenta creada. Revisa tu correo para confirmarla y luego inicia sesión.");
          setTab("login");
          return;
        }
      } else {
        await auth.signIn(email.trim(), password);
      }
      navigate("/examenes", { replace: true });
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
        <div className="tabs">
          <button type="button" className={tab === "login" ? "on" : ""} onClick={() => { setTab("login"); setError(""); }}>
            Iniciar sesión
          </button>
          <button type="button" className={signup ? "on" : ""} onClick={() => { setTab("signup"); setError(""); }}>
            Crear cuenta
          </button>
        </div>
        <label htmlFor="email">Correo</label>
        <input id="email" type="email" autoComplete="username" required value={email}
               onChange={(e) => setEmail(e.target.value)} />
        <label htmlFor="password">Contraseña</label>
        <input id="password" type="password" required value={password}
               autoComplete={signup ? "new-password" : "current-password"}
               onChange={(e) => setPassword(e.target.value)} />
        {signup && (
          <>
            <label htmlFor="password2">Repite la contraseña</label>
            <input id="password2" type="password" autoComplete="new-password" required value={password2}
                   onChange={(e) => setPassword2(e.target.value)} />
            <p className="note">Mínimo {MIN_PASSWORD} caracteres.</p>
          </>
        )}
        {error && <p className="error">{error}</p>}
        {info && <p className="ok">{info}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Un momento…" : signup ? "Crear cuenta" : "Iniciar sesión"}
        </button>
      </form>
    </main>
  );
}
