// Rutas de la web. Todo menos /login pide sesión (salvo en modo local).
//   /login                iniciar sesión / crear cuenta / olvidé mi contraseña (?olvide)
//   /restablecer          contraseña nueva (enlace del correo de recuperación)
//   /examenes             Mis exámenes
//   /examenes/nuevo       crear examen
//   /examenes/:id         editar examen
//   /examenes/:id/resultados        hojas calificadas, exportar, duplicados
//   /examenes/:id/resultados/:rid   una hoja: imagen y corrección a mano
//   /calificar            cámara en vivo, tomar foto o subirla (?examen=<id>)
import { Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { RESET_PATH, useAuth } from "./lib/auth.jsx";
import ExamEditor from "./pages/ExamEditor.jsx";
import Exams from "./pages/Exams.jsx";
import Grade from "./pages/Grade.jsx";
import Login from "./pages/Login.jsx";
import ResetPassword from "./pages/ResetPassword.jsx";
import ResultDetail from "./pages/ResultDetail.jsx";
import Results from "./pages/Results.jsx";

export default function App() {
  const auth = useAuth();
  if (auth.loading) return <main><div className="card center"><div className="spinner" /></div></main>;
  if (auth.error) return <main><div className="card"><h1>Calificador</h1><p>{auth.error}</p></div></main>;

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path={RESET_PATH} element={<ResetPassword />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route path="/examenes" element={<Exams />} />
        <Route path="/examenes/nuevo" element={<ExamEditor />} />
        <Route path="/examenes/:id" element={<ExamEditor />} />
        <Route path="/examenes/:id/resultados" element={<Results />} />
        <Route path="/examenes/:id/resultados/:rid" element={<ResultDetail />} />
        <Route path="/calificar" element={<Grade />} />
      </Route>
      <Route path="*" element={<Navigate to="/examenes" replace />} />
    </Routes>
  );
}

function RequireAuth({ children }) {
  const { local, session } = useAuth();
  if (!local && !session) return <Navigate to="/login" replace />;
  return children;
}

function Layout() {
  const { local, email, signOut } = useAuth();
  const navigate = useNavigate();
  return (
    <>
      <header className="nav">
        <nav>
          <NavLink to="/examenes">Mis exámenes</NavLink>
          <NavLink to="/calificar">Calificar</NavLink>
        </nav>
        {!local && (
          <div className="nav-user">
            <span className="note">{email}</span>
            <button className="small secondary" onClick={async () => { await signOut(); navigate("/login"); }}>
              Salir
            </button>
          </div>
        )}
      </header>
      <main><Outlet /></main>
    </>
  );
}
