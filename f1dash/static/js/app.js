// Page behaviour: theme toggle, local times, countdowns, filter forms.
(function () {
  "use strict";

  const MAX_FOCUS = 3;

  function currentTheme() {
    const set = document.documentElement.dataset.theme;
    if (set) return set;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function toggleTheme() {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
    document.dispatchEvent(new CustomEvent("themechange"));
  }

  function localTimes(root) {
    root.querySelectorAll("[data-localtime]").forEach((el) => {
      const d = new Date(el.dataset.localtime);
      if (isNaN(d)) return;
      const fmt = el.dataset.format;
      const opts =
        fmt === "date"
          ? { weekday: "short", day: "numeric", month: "short", year: "numeric" }
          : fmt === "time"
            ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
            : { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
      el.textContent = d.toLocaleString(undefined, opts);
      el.title = d.toISOString();
    });
  }

  function tickCountdowns() {
    document.querySelectorAll("[data-countdown]").forEach((el) => {
      const ms = new Date(el.dataset.countdown) - Date.now();
      if (isNaN(ms)) return;
      if (ms <= 0) {
        el.textContent = "Under way";
        return;
      }
      const s = Math.floor(ms / 1000);
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      const pad = (n) => String(n).padStart(2, "0");
      el.textContent = d > 0 ? `${d}d ${pad(h)}h ${pad(m)}m` : `${pad(h)}:${pad(m)}:${pad(sec)}`;
    });
  }

  function wireForms(root) {
    root.querySelectorAll("form[data-focus-form]").forEach((form) => {
      if (form.dataset.wired) return;
      form.dataset.wired = "1";
      // Selection order decides colour slots, so a driver keeps its colour
      // when others are added or removed.
      let order = (form.dataset.initial || "").split(",").filter(Boolean);
      form.addEventListener("change", (ev) => {
        const box = ev.target;
        if (box.name !== "focus") return;
        order = order.filter((v) => v !== box.value);
        if (box.checked) order.push(box.value);
        while (order.length > MAX_FOCUS) {
          const dropped = order.shift();
          form.querySelector(`input[name=focus][value="${dropped}"]`).checked = false;
        }
        form.querySelectorAll("input[name=focus]").forEach((input) => {
          const key = input.parentElement.querySelector(".key");
          const slot = order.indexOf(input.value) + 1;
          key.className = slot ? `key s${slot}` : "key";
        });
        const focus = order.join(",");
        const page = new URL(location.href);
        if (focus) page.searchParams.set("focus", focus);
        else page.searchParams.delete("focus");
        history.replaceState(history.state, "", page);
        // Refetch only the panels that depend on the highlighted drivers;
        // each keeps its current render until the new one arrives.
        document.querySelectorAll(".panel[data-focus-aware]").forEach((panel) => {
          const url = new URL(panel.getAttribute("hx-get"), location.href);
          if (focus) url.searchParams.set("focus", focus);
          else url.searchParams.delete("focus");
          panel.setAttribute("hx-get", url.pathname + url.search);
          htmx.ajax("GET", url.pathname + url.search, { target: panel, swap: "innerHTML" });
        });
      });
    });
    root.querySelectorAll("select[data-year-select]").forEach((sel) => {
      if (sel.dataset.wired) return;
      sel.dataset.wired = "1";
      sel.addEventListener("change", () => sel.form.requestSubmit());
    });
  }

  function init(root) {
    localTimes(root);
    wireForms(root);
    tickCountdowns();
  }

  function bindThemeButton() {
    const btn = document.getElementById("theme-toggle");
    if (btn && !btn.dataset.wired) {
      btn.dataset.wired = "1";
      btn.addEventListener("click", toggleTheme);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindThemeButton();
    init(document);
    setInterval(tickCountdowns, 1000);
  });
  document.addEventListener("htmx:load", (ev) => init(ev.detail.elt));
  document.addEventListener("htmx:sseMessage", (ev) => localTimes(ev.detail.elt || document));
  // Boosted navigation swaps the body, so rebind the header button.
  document.addEventListener("htmx:afterSettle", bindThemeButton);
  window.F1Dash = { currentTheme };
})();
