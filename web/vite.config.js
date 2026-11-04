// Vite: en desarrollo (npm run dev) sirve la web en http://localhost:5173 y
// manda /api al servidor de Python (python -m api.serve, HTTPS en el 8000).
// npm run build deja la web en dist/, que sirve el mismo api.serve.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "https://localhost:8000", secure: false, changeOrigin: true },
    },
  },
  test: { environment: "node" },
});
