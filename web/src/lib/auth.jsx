// Sesión del usuario. Pregunta a /api/config si el servidor usa Supabase:
// - sí: crea el cliente de supabase-js (inicia sesión, crea cuenta, cierra
//   sesión, recupera la contraseña; el token se guarda y se renueva solo).
//   El enlace del correo de recuperación regresa a /restablecer con la
//   sesión en la URL; supabase-js la lee sola.
// - no (modo local con SQLite): no hay login, todo es del usuario local.
import { createClient } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { configureApi } from "./api.js";
import { log } from "./log.js";

const AuthContext = createContext(null);
export const RESET_PATH = "/restablecer";

// Si el enlace del correo venció, Supabase regresa con #error_code=... en la
// URL; se lee antes de que supabase-js la limpie.
function linkErrorFromUrl() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const code = params.get("error_code");
  if (!code) return null;
  log.warn("el enlace de Supabase trae error:", code);
  return code === "otp_expired"
    ? "El enlace ya venció o ya se usó. Pide otro."
    : params.get("error_description") || "El enlace no es válido. Pide otro.";
}

export function AuthProvider({ children }) {
  const [state, setState] = useState({ loading: true, local: false, session: null, error: null,
                                      linkError: null });
  const [client, setClient] = useState(null);

  useEffect(() => {
    let unsubscribe = () => {};
    (async () => {
      const config = await fetch("/api/config").then((r) => r.json());
      if (!config.auth) {
        log.info("modo local: sin inicio de sesión");
        setState({ loading: false, local: true, session: null, error: null });
        return;
      }
      const linkError = linkErrorFromUrl();
      const sb = createClient(config.supabase_url, config.publishable_key);
      configureApi(sb, () => sb.auth.signOut().catch(() => {}));
      setClient(sb);
      const { data } = await sb.auth.getSession();
      log.info("sesión:", data.session ? data.session.user.email : "ninguna");
      setState({ loading: false, local: false, session: data.session, error: null, linkError });
      const { data: sub } = sb.auth.onAuthStateChange((event, session) => {
        log.debug("cambio de sesión:", event);
        if (event === "PASSWORD_RECOVERY") log.info("entró con el enlace para cambiar la contraseña");
        setState((s) => ({ ...s, session }));
      });
      unsubscribe = () => sub.subscription.unsubscribe();
    })().catch((err) => {
      log.warn("no se pudo preparar la sesión", err);
      setState({ loading: false, local: false, session: null,
                 error: "No hay conexión con el servidor." });
    });
    return () => unsubscribe();
  }, []);

  const value = useMemo(() => ({
    ...state,
    email: state.session?.user?.email ?? null,
    async signIn(email, password) {
      log.info("iniciando sesión:", email);
      const { error } = await client.auth.signInWithPassword({ email, password });
      if (error) throw new Error(authErrorText(error));
    },
    // Regresa true si ya entró; false si Supabase pide confirmar el correo.
    async signUp(email, password) {
      log.info("creando cuenta:", email);
      const { data, error } = await client.auth.signUp({ email, password });
      if (error) throw new Error(authErrorText(error));
      // Si el correo ya existía, Supabase no da error pero tampoco identidades
      if (data.user && data.user.identities?.length === 0)
        throw new Error("Ya existe una cuenta con ese correo. Inicia sesión.");
      log.info("cuenta creada:", data.session ? "con sesión" : "falta confirmar el correo");
      return Boolean(data.session);
    },
    // Manda el correo con el enlace para poner una contraseña nueva.
    async requestPasswordReset(email) {
      log.info("pidiendo enlace de recuperación para", email);
      const { error } = await client.auth.resetPasswordForEmail(email, {
        redirectTo: window.location.origin + RESET_PATH,
      });
      if (error) throw new Error(authErrorText(error));
    },
    async updatePassword(password) {
      log.info("cambiando la contraseña");
      const { error } = await client.auth.updateUser({ password });
      if (error) throw new Error(authErrorText(error));
    },
    async signOut() {
      log.info("cerrando sesión");
      await client?.auth.signOut();
    },
  }), [state, client]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

function authErrorText(error) {
  const msg = (error.message || "").toLowerCase();
  if (error.code === "invalid_credentials" || msg.includes("invalid login"))
    return "Correo o contraseña incorrectos.";
  if (error.code === "user_already_exists" || msg.includes("already registered"))
    return "Ya existe una cuenta con ese correo. Inicia sesión.";
  if (error.code === "weak_password" || msg.includes("password should be"))
    return "La contraseña es muy corta (mínimo 6 caracteres).";
  if (error.code === "email_address_invalid" || msg.includes("invalid format"))
    return "El correo no es válido.";
  if (error.code === "signup_disabled") return "El registro de cuentas está desactivado.";
  if (error.code === "same_password") return "La contraseña nueva debe ser distinta a la anterior.";
  if (error.code === "over_email_send_rate_limit" || msg.includes("email rate limit"))
    return "Se mandaron demasiados correos; espera un rato y vuelve a intentar.";
  if (error.code === "session_not_found" || error.code === "otp_expired")
    return "El enlace ya venció. Pide otro.";
  if (error.status === 429) return "Demasiados intentos; espera un momento.";
  return error.message;
}
