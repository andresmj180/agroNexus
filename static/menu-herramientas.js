(() => {
  const HERRAMIENTAS = [
    {
      id: "ventana_secado",
      titulo: "Ventana de secado",
      sub: "Sol para secar el café",
      icono: "/img/icono/ventana_secado",
      boton: "Secado",
    },
    {
      id: "monitoreo_lote",
      titulo: "Monitoreo de roya y broca",
      sub: "Registra y compara con el umbral",
      icono: "/img/icono/monitoreo_lote",
      boton: "Roya y broca",
    },
    {
      id: "plan_semana",
      titulo: "Plan de la semana",
      sub: "Labores según clima y lote",
      icono: "/img/icono/plan_semana",
      boton: "Plan semanal",
    },
  ];

  const FORMAS = {
    ventana_secado: `
      <p class="agro-hint">Con el pronóstico, indica qué días hay sol suficiente para secar el café.</p>
      <label for="agro-municipio">Municipio o vereda (opcional)</label>
      <input id="agro-municipio" name="municipio" placeholder="Ej. Manizales, Chinchiná, Pitalito">
      <div class="agro-row">
        <div>
          <label for="agro-lat">Latitud (opcional)</label>
          <input id="agro-lat" name="latitud" inputmode="decimal" placeholder="5.07">
        </div>
        <div>
          <label for="agro-lon">Longitud (opcional)</label>
          <input id="agro-lon" name="longitud" inputmode="decimal" placeholder="-75.52">
        </div>
      </div>
      <button class="agro-go" type="submit">Ver ventana de secado</button>
    `,
    monitoreo_lote: `
      <p class="agro-hint">Reporta el porcentaje de hojas con roya o de frutos con broca. Se guarda y se compara con el umbral de Cenicafé.</p>
      <label for="agro-lote">Lote</label>
      <input id="agro-lote" name="lote" required placeholder="Ej. El Roble — tabla 3">
      <div class="agro-row">
        <div>
          <label for="agro-roya">% hojas con roya</label>
          <input id="agro-roya" name="roya" type="number" min="0" max="100" step="0.1" placeholder="0">
        </div>
        <div>
          <label for="agro-broca">% frutos con broca</label>
          <input id="agro-broca" name="broca" type="number" min="0" max="100" step="0.1" placeholder="0">
        </div>
      </div>
      <label for="agro-fecha">Fecha del muestreo (opcional)</label>
      <input id="agro-fecha" name="fecha" type="date">
      <button class="agro-go" type="submit">Guardar y evaluar</button>
    `,
    plan_semana: `
      <p class="agro-hint">Junta pronóstico, etapa del lote e historial y entrega una lista corta de labores.</p>
      <label for="agro-lote-p">Lote</label>
      <input id="agro-lote-p" name="lote" placeholder="Ej. El Roble — tabla 3">
      <label for="agro-etapa">Etapa del lote</label>
      <select id="agro-etapa" name="etapa">
        <option value="crecimiento">Crecimiento vegetativo</option>
        <option value="floracion">Floración</option>
        <option value="llenado">Llenado de grano</option>
        <option value="maduracion">Maduración</option>
        <option value="cosecha" selected>Cosecha</option>
        <option value="postcosecha">Poscosecha / secado</option>
      </select>
      <label for="agro-municipio-p">Municipio (opcional)</label>
      <input id="agro-municipio-p" name="municipio" placeholder="Ej. Manizales">
      <button class="agro-go" type="submit">Armar plan de la semana</button>
    `,
  };

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  const barra = el('<nav class="agro-hb" aria-label="Herramientas del cafetal"></nav>');
  HERRAMIENTAS.forEach((h) => {
    const b = el(
      `<button type="button" data-id="${h.id}">
         <img src="${h.icono}" alt="">
         <span>${h.boton}</span>
       </button>`
    );
    b.addEventListener("click", () => abrir(h.id));
    barra.appendChild(b);
  });

  const fondo = el('<div class="agro-sheet-bg" hidden></div>');
  fondo.addEventListener("click", cerrar);

  const sheet = el(
    `<aside class="agro-sheet" role="dialog" aria-modal="true" aria-labelledby="agro-sheet-title">
       <header class="agro-sheet-head">
         <img id="agro-sheet-icon" alt="">
         <div>
           <h2 id="agro-sheet-title"></h2>
           <p id="agro-sheet-sub"></p>
         </div>
         <button class="agro-sheet-x" type="button" aria-label="Cerrar">×</button>
       </header>
       <div class="agro-sheet-body">
         <form id="agro-form"></form>
         <div class="agro-out" id="agro-out"></div>
       </div>
     </aside>`
  );
  sheet.querySelector(".agro-sheet-x").addEventListener("click", cerrar);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") cerrar();
  });

  let actual = "";

  function abrir(id) {
    const h = HERRAMIENTAS.find((x) => x.id === id);
    if (!h) return;
    actual = id;
    barra.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("is-on", b.dataset.id === id);
    });
    sheet.querySelector("#agro-sheet-icon").src = h.icono;
    sheet.querySelector("#agro-sheet-title").textContent = h.titulo;
    sheet.querySelector("#agro-sheet-sub").textContent = h.sub;
    const form = sheet.querySelector("#agro-form");
    form.innerHTML = FORMAS[id];
    form.onsubmit = enviar;
    const out = sheet.querySelector("#agro-out");
    out.className = "agro-out";
    out.textContent = "";
    fondo.hidden = false;
    fondo.classList.add("is-open");
    sheet.classList.add("is-open");
  }

  function cerrar() {
    actual = "";
    barra.querySelectorAll("button").forEach((b) => b.classList.remove("is-on"));
    fondo.classList.remove("is-open");
    sheet.classList.remove("is-open");
    setTimeout(() => {
      if (!sheet.classList.contains("is-open")) fondo.hidden = true;
    }, 280);
  }

  async function enviar(ev) {
    ev.preventDefault();
    const form = ev.currentTarget;
    const btn = form.querySelector(".agro-go");
    const out = sheet.querySelector("#agro-out");
    const datos = {};
    new FormData(form).forEach((v, k) => {
      if (String(v).trim() !== "") datos[k] = String(v).trim();
    });
    datos.herramienta = actual;
    btn.disabled = true;
    out.className = "agro-out is-on";
    out.textContent = "Consultando…";
    try {
      const r = await fetch("/herramienta", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(datos),
      });
      const j = await r.json();
      if (!r.ok || j.error) throw new Error(j.error || "No se pudo completar");
      out.className = "agro-out is-on";
      out.textContent = j.resultado || JSON.stringify(j, null, 2);
    } catch (err) {
      out.className = "agro-out is-on is-err";
      out.textContent = err.message || String(err);
    } finally {
      btn.disabled = false;
    }
  }

  function montar() {
    document.body.append(barra, fondo, sheet);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", montar);
  } else {
    montar();
  }
})();
