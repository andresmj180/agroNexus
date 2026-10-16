(() => {
  const SID_KEY = "agro_sid";
  const ABIERTO_KEY = "agro_hist_abierto";

  const $ = (sel, root = document) => root.querySelector(sel);

  function sidActual() {
    let sid = window.__agroSid || localStorage.getItem(SID_KEY);
    if (!sid) {
      sid = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
      localStorage.setItem(SID_KEY, sid);
    }
    window.__agroSid = sid;
    return sid;
  }

  function setSid(sid) {
    window.__agroSid = sid;
    localStorage.setItem(SID_KEY, sid);
  }

  async function api(ruta, extra = {}) {
    const r = await fetchOrig(ruta, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: sidActual(), ...extra }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  function contenedorMensajes() {
    return (
      $("#messages") ||
      $("#chat-messages") ||
      $("#log") ||
      $(".chat-log") ||
      $("[data-chat]") ||
      $(".messages")
    );
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function pintarMensajes(msgs) {
    const box = contenedorMensajes();
    if (!box) return;
    box.innerHTML = "";
    (msgs || []).forEach((m) => {
      const div = document.createElement("div");
      div.className = `msg ${m.role} agro-hist-msg`;
      const rol = m.role === "user" ? "Tú" : "agroNexus";
      div.innerHTML = `<div class="agro-hist-rol">${rol}</div><div class="agro-hist-txt">${escapeHtml(m.content).replace(/\n/g, "<br>")}</div>`;
      box.appendChild(div);
    });
    box.scrollTop = box.scrollHeight;
  }

  function fmtFecha(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("es", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function ui() {
    if ($("#agro-historial")) return;
    const panel = document.createElement("aside");
    panel.id = "agro-historial";
    panel.innerHTML = `
      <div class="agro-hist-cabecera">
        <h2>Conversaciones</h2>
        <button type="button" class="agro-hist-cerrar" title="Cerrar">×</button>
      </div>
      <div class="agro-hist-acciones">
        <button type="button" class="agro-hist-nueva">+ Nueva conversación</button>
        <input class="agro-hist-buscar" type="search" placeholder="Buscar…">
      </div>
      <div class="agro-hist-lista"></div>
    `;
    document.body.appendChild(panel);

    const btn = document.createElement("button");
    btn.id = "agro-historial-toggle";
    btn.type = "button";
    btn.title = "Historial";
    btn.textContent = "☰";
    document.body.appendChild(btn);

    const setAbierto = (abierto) => {
      panel.classList.toggle("agro-cerrado", !abierto);
      document.body.classList.toggle("agro-hist-abierto", abierto);
      localStorage.setItem(ABIERTO_KEY, abierto ? "1" : "0");
    };
    const abiertoIni = localStorage.getItem(ABIERTO_KEY) !== "0";
    setAbierto(abiertoIni);

    btn.addEventListener("click", () => setAbierto(panel.classList.contains("agro-cerrado")));
    $(".agro-hist-cerrar", panel).addEventListener("click", () => setAbierto(false));
    $(".agro-hist-nueva", panel).addEventListener("click", nueva);
    $(".agro-hist-buscar", panel).addEventListener("input", (e) => renderLista(e.target.value));
  }

  let cache = [];

  function renderLista(filtro = "") {
    const lista = $(".agro-hist-lista");
    if (!lista) return;
    const q = filtro.trim().toLowerCase();
    const items = cache.filter((c) => !q || (c.titulo || "").toLowerCase().includes(q));
    if (!items.length) {
      lista.innerHTML = `<div class="agro-hist-vacio">${cache.length ? "Sin coincidencias." : "Aún no hay conversaciones."}</div>`;
      return;
    }
    const actual = sidActual();
    lista.innerHTML = "";
    items.forEach((c) => {
      const el = document.createElement("div");
      el.className = "agro-hist-item" + (c.id === actual ? " activo" : "");
      el.dataset.id = c.id;
      el.innerHTML = `
        <div class="agro-hist-item-cuerpo">
          <div class="agro-hist-titulo" title="Doble clic para renombrar">${escapeHtml(c.titulo || "Conversación")}</div>
          <div class="agro-hist-fecha">${escapeHtml(fmtFecha(c.actualizada))}</div>
        </div>
        <button type="button" class="agro-hist-borrar" title="Eliminar">×</button>
      `;
      el.addEventListener("click", (ev) => {
        if (ev.target.closest(".agro-hist-borrar")) return;
        cargar(c.id);
      });
      $(".agro-hist-borrar", el).addEventListener("click", (ev) => {
        ev.stopPropagation();
        borrar(c.id, c.titulo);
      });
      $(".agro-hist-titulo", el).addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        empezarRename(el, c);
      });
      lista.appendChild(el);
    });
  }

  function empezarRename(el, conv) {
    const tituloEl = $(".agro-hist-titulo", el);
    const input = document.createElement("input");
    input.className = "agro-hist-edit";
    input.value = conv.titulo || "";
    tituloEl.replaceWith(input);
    input.focus();
    input.select();
    const guardar = async () => {
      input.onblur = null;
      const nuevo = input.value.trim();
      if (nuevo && nuevo !== conv.titulo) {
        await api("/conversacion/renombrar", { session: conv.id, titulo: nuevo });
        await refrescar();
      } else {
        renderLista($(".agro-hist-buscar")?.value || "");
      }
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") input.blur();
      if (e.key === "Escape") {
        input.onblur = null;
        renderLista($(".agro-hist-buscar")?.value || "");
      }
    });
    input.onblur = guardar;
  }

  async function refrescar() {
    try {
      const data = await api("/conversaciones");
      cache = data.conversaciones || [];
      renderLista($(".agro-hist-buscar")?.value || "");
    } catch (e) {
      console.warn("historial:", e);
    }
  }

  async function nueva() {
    const data = await api("/conversacion/nueva");
    setSid(data.id);
    pintarMensajes([]);
    await refrescar();
  }

  async function cargar(id) {
    setSid(id);
    const data = await api("/conversacion/cargar", { session: id });
    pintarMensajes(data.mensajes || []);
    renderLista($(".agro-hist-buscar")?.value || "");
  }

  async function borrar(id, titulo) {
    if (!confirm(`¿Eliminar “${titulo || "esta conversación"}”?`)) return;
    await api("/conversacion/eliminar", { session: id });
    if (sidActual() === id) {
      await nueva();
    } else {
      await refrescar();
    }
  }

  const fetchOrig = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const esApi = ["/chat", "/reset", "/conversaciones", "/conversacion/"].some((p) =>
      String(url).includes(p)
    );
    if (esApi && init && typeof init.body === "string") {
      try {
        const body = JSON.parse(init.body);
        if (body && typeof body === "object" && !body.session) {
          body.session = sidActual();
          init = { ...init, body: JSON.stringify(body) };
        } else if (body && body.session) {
          setSid(body.session);
        }
      } catch (_) { /* cuerpo no JSON */ }
    }
    const res = await fetchOrig(input, init);
    if (String(url).includes("/chat") || String(url).includes("/reset")) {
      setTimeout(refrescar, 200);
    }
    return res;
  };

  function init() {
    sidActual();
    ui();
    refrescar();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
