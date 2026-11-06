// Imagen de revisión de una hoja guardada. Se pide con el token (apiFetch),
// por eso no basta con <img src>.
import { useEffect, useState } from "react";
import { apiFetch } from "../lib/api.js";
import { log } from "../lib/log.js";

export default function ReviewImage({ resultId, className = "review", alt = "Imagen de revisión" }) {
  const [url, setUrl] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let objectUrl = null;
    let cancelled = false;
    setUrl(null);
    setFailed(false);
    apiFetch(`/api/results/${resultId}/review.jpg`)
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`Error ${resp.status}`);
        const blob = await resp.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
        log.debug("imagen de revisión", resultId, blob.size, "bytes");
      })
      .catch((err) => {
        if (!cancelled) setFailed(true);
        log.warn("no se pudo cargar la imagen de", resultId, err.message);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [resultId]);

  if (failed) return <div className="note">Sin imagen de revisión.</div>;
  if (!url) return <div className={`${className} img-loading`} />;
  return <img className={className} src={url} alt={alt} />;
}
