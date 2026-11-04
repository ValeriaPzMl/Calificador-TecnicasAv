// Logs de la web en la consola del navegador, como los -v del backend.
// info: el flujo (qué pantalla, qué petición); debug: el detalle.
// Para ver el detalle: localStorage.setItem("calificador.debug", "1") y recargar.
const PREFIX = "[calificador]";

function debugOn() {
  try { return globalThis.localStorage?.getItem("calificador.debug") === "1"; }
  catch { return false; }
}

export const log = {
  info: (...args) => console.info(PREFIX, ...args),
  warn: (...args) => console.warn(PREFIX, ...args),
  debug: (...args) => { if (debugOn()) console.debug(PREFIX, ...args); },
};
