// Configuración de la cámara en vivo. Es lo único que habría que ajustar
// por dispositivo.
export const CAMERA_CONFIG = {
  facingMode: "environment", // cámara trasera en el celular; en la compu se ignora
  width: 1920, height: 1080, // resolución que se le pide (el navegador da la más cercana)
  sendMaxSide: 1600,         // el cuadro se reduce a esto antes de mandarlo
  jpegQuality: 0.85,
  continuousFocus: true,     // pedir enfoque continuo (Android Chrome lo soporta)
};

// Colores del contorno según el estado que manda el servidor (core/live.py)
export const STATE_COLORS = {
  buscando_hoja: "#e04848", examen_desconocido: "#e04848",
  sin_qr: "#ffa000", borrosa: "#ffa000", leyendo: "#4caf50", lista: "#4caf50",
};
