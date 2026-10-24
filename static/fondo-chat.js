(() => {
  const TRANSPARENTES = [
    "main",
    "#app",
    "#root",
    ".app",
    ".layout",
    ".chat",
    "#chat",
    ".chat-wrap",
    ".chat-container",
    ".mensajes",
    "#mensajes",
    ".messages",
    "#messages",
    ".conversation",
    ".thread",
    ".panel",
    ".content",
    ".columna",
    ".columna-chat",
  ];

  function qsAll(sel) {
    try {
      return Array.from(document.querySelectorAll(sel));
    } catch {
      return [];
    }
  }

  function aplicar() {
    document.documentElement.classList.add("agro-fondo-chat");
    document.body.classList.add("agro-fondo-chat");

    const vistos = new Set();
    TRANSPARENTES.forEach((sel) => {
      qsAll(sel).forEach((n) => {
        if (vistos.has(n) || n === document.body) return;
        vistos.add(n);
        const cs = getComputedStyle(n);
        const bg = (cs.backgroundColor || "").replace(/\s/g, "");
        if (bg === "rgba(0,0,0,0)" || bg === "transparent") return;
        n.classList.add("agro-fondo-transparente");
      });
    });

    qsAll("header, nav, aside, footer, .sidebar, #sidebar, .barra, .topbar").forEach(
      (n) => {
        if (n.classList.contains("agro-hb") || n.classList.contains("agro-sheet")) return;
        n.classList.add("agro-fondo-panel");
      }
    );

    qsAll(
      ".msg, .message, .burbuja, .bubble, [class*='mensaje'], [class*='message']"
    ).forEach((n) => {
      const t = (n.className || "").toLowerCase();
      if (t.includes("user") || t.includes("usuario") || t.includes("mine")) {
        n.classList.add("agro-fondo-burbuja-user");
      } else {
        n.classList.add("agro-fondo-burbuja-bot");
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", aplicar);
  } else {
    aplicar();
  }
  window.addEventListener("load", aplicar);
})();
