(() => {
  "use strict";

  const MAX_ADJUNTOS = 8;
  const MAX_BYTES = 20 * 1024 * 1024;
  const SESION_KEY = "agronexus_sid";

  const $ = (id) => document.getElementById(id);
  const mensajesEl = $("mensajes");
  const formEl = $("formulario");
  const entradaEl = $("entrada");
  const btnEnviar = $("btnEnviar");
  const btnAdjuntar = $("btnAdjuntar");
  const inputArchivos = $("inputArchivos");
  const previewEl = $("adjuntosPreview");
  const btnNueva = $("btnNueva");

  let sid = localStorage.getItem(SESION_KEY);
  if (!sid) {
    sid = crypto.randomUUID();
    localStorage.setItem(SESION_KEY, sid);
  }

  let adjuntos = [];
  let ocupado = false;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatear(texto) {
    return escapeHtml(texto || "").replace(/\n/g, "<br>");
  }

  function scrollFinal() {
    mensajesEl.scrollTop = mensajesEl.scrollHeight;
  }

  function burbuja(rol, html, herramientas) {
    const art = document.createElement("article");
    art.className = "msg msg-" + rol;
    const cuerpo = document.createElement("div");
    cuerpo.className = "msg-cuerpo";
    cuerpo.innerHTML = html;
    art.appendChild(cuerpo);
    if (herramientas && herramientas.length) {
      const ul = document.createElement("ul");
      ul.className = "msg-tools";
      for (const t of herramientas) {
        const li = document.createElement("li");
        li.textContent = t;
        ul.appendChild(li);
      }
      art.appendChild(ul);
    }
    mensajesEl.appendChild(art);
    scrollFinal();
    return art;
  }

  function mostrarBienvenida() {
    if (mensajesEl.childElementCount) return;
    burbuja(
      "asistente",
      "Hola, soy <strong>agroNexus</strong>. Escribe tu consulta o adjunta archivos, imágenes, audio o video."
    );
  }

  function leerArchivo(file) {
    return new Promise((resolve, reject) => {
      if (file.size > MAX_BYTES) {
        reject(new Error('"' + file.name + '" supera 20 MB.'));
        return;
      }
      const reader = new FileReader();
      reader.onload = () =>
        resolve({
          name: file.name,
          type: file.type || "application/octet-stream",
          data: reader.result,
          size: file.size,
        });
      reader.onerror = () => reject(new Error("No se pudo leer " + file.name));
      reader.readAsDataURL(file);
    });
  }

  function renderPreview() {
    previewEl.innerHTML = "";
    previewEl.hidden = adjuntos.length === 0;
    adjuntos.forEach((a, i) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      const nom = document.createElement("span");
      nom.textContent = a.name;
      const x = document.createElement("button");
      x.type = "button";
      x.className = "chip-x";
      x.setAttribute("aria-label", "Quitar " + a.name);
      x.textContent = "×";
      x.addEventListener("click", () => {
        adjuntos.splice(i, 1);
        renderPreview();
      });
      chip.append(nom, x);
      previewEl.appendChild(chip);
    });
  }

  async function agregarArchivos(files) {
    const lista = Array.from(files || []);
    for (const f of lista) {
      if (adjuntos.length >= MAX_ADJUNTOS) {
        alert("Máximo 8 archivos por mensaje.");
        break;
      }
      try {
        adjuntos.push(await leerArchivo(f));
      } catch (e) {
        alert(e.message);
      }
    }
    renderPreview();
  }

  function autoaltura() {
    entradaEl.style.height = "auto";
    entradaEl.style.height = Math.min(entradaEl.scrollHeight, 160) + "px";
  }

  function setOcupado(v) {
    ocupado = v;
    btnEnviar.disabled = v;
    entradaEl.disabled = v;
    btnAdjuntar.disabled = v;
    btnNueva.disabled = v;
  }

  async function enviar(ev) {
    if (ev) ev.preventDefault();
    if (ocupado) return;
    const texto = entradaEl.value.trim();
    if (!texto && !adjuntos.length) return;

    const payloadAdjuntos = adjuntos.map(({ name, type, data }) => ({
      name,
      type,
      data,
    }));
    const nombres = adjuntos.map((a) => a.name);

    burbuja(
      "usuario",
      formatear(texto) +
        (nombres.length
          ? '<div class="msg-files">' +
            nombres.map(escapeHtml).join(" · ") +
            "</div>"
          : "")
    );

    entradaEl.value = "";
    autoaltura();
    adjuntos = [];
    renderPreview();

    const espera = burbuja(
      "asistente",
      '<span class="pensando" aria-label="Pensando"><i></i><i></i><i></i></span>'
    );
    setOcupado(true);

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session: sid,
          message: texto,
          attachments: payloadAdjuntos,
        }),
      });
      const raw = await res.text();
      let data;
      try {
        data = JSON.parse(raw);
      } catch {
        data = { error: raw || "Respuesta inválida" };
      }
      espera.remove();
      if (!res.ok || data.error) {
        burbuja("error", escapeHtml(data.error || "Error de red"));
      } else {
        burbuja("asistente", formatear(data.reply || ""), data.tools || []);
      }
    } catch (e) {
      espera.remove();
      burbuja("error", escapeHtml(e.message || "No se pudo conectar"));
    } finally {
      setOcupado(false);
      entradaEl.focus();
    }
  }

  async function nuevaConversacion() {
    if (ocupado) return;
    try {
      await fetch("/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session: sid }),
      });
    } catch (_) {}
    sid = crypto.randomUUID();
    localStorage.setItem(SESION_KEY, sid);
    mensajesEl.innerHTML = "";
    adjuntos = [];
    renderPreview();
    mostrarBienvenida();
    entradaEl.focus();
  }

  formEl.addEventListener("submit", enviar);
  btnNueva.addEventListener("click", nuevaConversacion);
  btnAdjuntar.addEventListener("click", () => inputArchivos.click());
  inputArchivos.addEventListener("change", (e) => {
    agregarArchivos(e.target.files);
    inputArchivos.value = "";
  });
  entradaEl.addEventListener("input", autoaltura);
  entradaEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      enviar();
    }
  });
  document.addEventListener("paste", (e) => {
    const files = [];
    for (const item of e.clipboardData?.items || []) {
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      agregarArchivos(files);
    }
  });

  document.addEventListener("dragover", (e) => {
    e.preventDefault();
    document.body.classList.add("arrastrando");
  });
  document.addEventListener("dragleave", (e) => {
    if (e.relatedTarget) return;
    document.body.classList.remove("arrastrando");
  });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    document.body.classList.remove("arrastrando");
    agregarArchivos(e.dataTransfer && e.dataTransfer.files);
  });

  mostrarBienvenida();
  entradaEl.focus();
})();
