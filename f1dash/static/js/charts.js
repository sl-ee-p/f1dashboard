// Chart rendering. The server sends semantic chart data (see services.py);
// this file turns it into themed Plotly figures and an accessible table view.
//
// Colour rules (dataviz method): up to three highlighted drivers take the
// validated categorical slots 1-3, in selection order; every other driver is a
// thin recessive "context" line. Text is always ink, never series colour.
(function () {
  "use strict";

  const COMPOUND_VAR = {
    SOFT: "--tyre-soft",
    MEDIUM: "--tyre-medium",
    HARD: "--tyre-hard",
    INTERMEDIATE: "--tyre-inter",
    WET: "--tyre-wet",
  };
  const COMPOUND_LETTER = { SOFT: "S", MEDIUM: "M", HARD: "H", INTERMEDIATE: "I", WET: "W" };
  const BAND_LABEL = { SC: "SC", VSC: "VSC", "Red flag": "Red flag" };

  function tokens() {
    const css = getComputedStyle(document.documentElement);
    const v = (name) => css.getPropertyValue(name).trim();
    return {
      surface: v("--surface-1"),
      surface2: v("--surface-2"),
      text: v("--text-primary"),
      text2: v("--text-secondary"),
      muted: v("--text-muted"),
      axis: v("--axis-muted"),
      grid: v("--grid"),
      baseline: v("--baseline"),
      context: v("--context"),
      warning: v("--warning"),
      critical: v("--critical"),
      slot: (n) => v(`--series-${n}`),
      compound: (c) => v(COMPOUND_VAR[c] || "--axis-muted"),
      seqLow: v("--seq-low"),
      seqHigh: v("--seq-high"),
      font: getComputedStyle(document.body).fontFamily,
    };
  }

  function alpha(hex, a) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // Ink colour that stays readable on top of a fill.
  function inkOn(hex) {
    const h = hex.replace("#", "");
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    return L > 0.35 ? "#0b0b0b" : "#ffffff";
  }

  function fmtLap(sec) {
    if (sec == null || isNaN(sec)) return "";
    const m = Math.floor(sec / 60);
    const s = sec - m * 60;
    return m ? `${m}:${s.toFixed(3).padStart(6, "0")}` : s.toFixed(3);
  }

  function baseLayout(t, extra) {
    const axis = {
      gridcolor: t.grid,
      gridwidth: 1,
      linecolor: t.baseline,
      zeroline: false,
      tickfont: { color: t.muted, size: 11 },
      title: { font: { color: t.muted, size: 11 } },
      automargin: true,
      showspikes: false,
    };
    return Object.assign(
      {
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { family: t.font, size: 12, color: t.text2 },
        margin: { l: 8, r: 8, t: 30, b: 8 },
        hoverlabel: {
          bgcolor: t.surface,
          bordercolor: t.baseline,
          font: { color: t.text, family: t.font, size: 12 },
        },
        legend: {
          orientation: "h",
          x: 0,
          y: 1.02,
          yanchor: "bottom",
          font: { color: t.text2, size: 11 },
          bgcolor: "rgba(0,0,0,0)",
        },
        xaxis: structuredClone(axis),
        yaxis: structuredClone(axis),
      },
      extra || {},
    );
  }

  const CONFIG = { displayModeBar: false, responsive: true };

  // ------------------------------------------------------------------ lines
  function lines(spec, t) {
    const traces = [];
    const shapes = [];
    const annotations = [];
    const context = spec.series.filter((s) => !s.slot);
    const focus = spec.series.filter((s) => s.slot).sort((a, b) => a.slot - b.slot);
    let ymax = 0;
    let ymin = Infinity;
    let xmax = 0;

    for (const s of context) {
      traces.push({
        type: "scatter",
        mode: "lines",
        x: s.x,
        y: s.y,
        name: s.code,
        line: { color: t.context, width: 1, shape: "linear" },
        showlegend: false,
        hovertemplate: `${s.code} %{y}<extra></extra>`,
      });
    }
    if (context.length) {
      traces.push({
        type: "scatter",
        mode: "lines",
        x: [null],
        y: [null],
        name: "Other drivers",
        line: { color: t.context, width: 1 },
        hoverinfo: "skip",
      });
    }
    const yspan = (() => {
      const ys = spec.series.flatMap((s) => s.y).filter((v) => v != null);
      return Math.max(...ys) - Math.min(...ys) || 1;
    })();
    const placed = [];
    let longest = 0;
    for (const s of focus) {
      const colour = t.slot(s.slot);
      traces.push({
        type: "scatter",
        mode: "lines",
        x: s.x,
        y: s.y,
        name: s.code,
        line: { color: colour, width: 2, shape: "linear" },
        hovertemplate: `<b>${s.code}</b> %{y}<extra></extra>`,
      });
      // End marker + direct label (label in ink, marker carries identity).
      const i = s.x.length - 1;
      if (i >= 0) {
        // Converging lines: keep the marker, leave the name to legend + tooltip.
        const clash = placed.some((y) => Math.abs(y - s.y[i]) < yspan * 0.05);
        if (!clash) {
          placed.push(s.y[i]);
          longest = Math.max(longest, s.code.length);
        }
        traces.push({
          type: "scatter",
          mode: clash ? "markers" : "markers+text",
          x: [s.x[i]],
          y: [s.y[i]],
          text: [s.code],
          textposition: "middle right",
          textfont: { color: t.text, size: 11 },
          marker: { color: colour, size: 9, line: { color: t.surface, width: 2 } },
          showlegend: false,
          hoverinfo: "skip",
          cliponaxis: false,
        });
      }
    }
    for (const s of spec.series) {
      for (const v of s.y) {
        if (v == null) continue;
        ymax = Math.max(ymax, v);
        ymin = Math.min(ymin, v);
      }
      xmax = Math.max(xmax, s.x[s.x.length - 1] || 0);
    }
    for (const b of spec.bands || []) {
      const colour = b.kind === "Red flag" ? t.critical : t.warning;
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "paper",
        x0: b.start - 0.5,
        x1: b.end + 0.5,
        y0: 0,
        y1: 1,
        fillcolor: alpha(colour, 0.14),
        line: { width: 0 },
        layer: "below",
      });
      annotations.push({
        xref: "x",
        yref: "paper",
        x: (b.start + b.end) / 2,
        y: 1,
        yanchor: "bottom",
        text: BAND_LABEL[b.kind] || b.kind,
        showarrow: false,
        font: { size: 10, color: t.muted },
      });
    }
    const layout = baseLayout(t, {
      hovermode: "x unified",
      shapes,
      annotations,
      margin: { l: 8, r: 16 + longest * 7, t: 36, b: 8 },
    });
    layout.xaxis.title.text = spec.x_title || "";
    layout.xaxis.range = [Math.min(...spec.series.map((s) => s.x[0] ?? 1)) - 0.5, xmax + 0.5];
    layout.xaxis.showspikes = true;
    layout.xaxis.spikemode = "across";
    layout.xaxis.spikethickness = 1;
    layout.xaxis.spikecolor = t.baseline;
    layout.xaxis.spikedash = "solid";
    layout.yaxis.title.text = spec.y_title || "";
    if (spec.y_integer && spec.y_reversed) {
      layout.yaxis.range = [ymax + 0.5, 0.5];
      layout.yaxis.tickvals = [1, 5, 10, 15, 20, 25].filter((v) => v <= ymax);
    } else if (spec.y_reversed) {
      const top = spec.y_max ? Math.min(spec.y_max, ymax) : ymax;
      layout.yaxis.range = [top * 1.04 + 1, Math.min(0, ymin) - 1];
    } else {
      layout.yaxis.rangemode = "tozero";
    }
    return { traces, layout };
  }

  // ---------------------------------------------------------- distribution
  function laptimeTicks(min, max) {
    const span = max - min;
    const step = span > 12 ? 2 : span > 5 ? 1 : 0.5;
    const vals = [];
    for (let v = Math.ceil(min / step) * step; v <= max; v += step) vals.push(+v.toFixed(2));
    return { tickvals: vals, ticktext: vals.map((v) => fmtLap(v).replace(/\.?0+$/, "")) };
  }

  function distribution(spec, t) {
    const traces = [];
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of spec.series) {
      const colour = s.slot ? t.slot(s.slot) : t.axis;
      for (const v of s.times) {
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
      traces.push({
        type: "box",
        name: s.code,
        y: s.times,
        x: s.times.map(() => s.code),
        customdata: s.laps.map((l, i) => [l, fmtLap(s.times[i])]),
        boxpoints: "outliers",
        line: { color: colour, width: s.slot ? 2 : 1 },
        fillcolor: s.slot ? alpha(colour, 0.18) : "rgba(0,0,0,0)",
        marker: { color: colour, size: 5, line: { color: t.surface, width: 1 } },
        hovertemplate: `<b>${s.code}</b> lap %{customdata[0]}: %{customdata[1]}<extra></extra>`,
        showlegend: false,
        whiskerwidth: 0.4,
      });
    }
    const layout = baseLayout(t, { hovermode: "closest", margin: { l: 8, r: 8, t: 10, b: 8 } });
    layout.xaxis.type = "category";
    layout.xaxis.tickfont = { color: t.text2, size: 10 };
    layout.xaxis.gridcolor = "rgba(0,0,0,0)";
    if (isFinite(lo)) {
      Object.assign(layout.yaxis, laptimeTicks(lo, hi));
      layout.yaxis.range = [lo - 0.3, hi + 0.3];
    }
    layout.yaxis.title.text = "Lap time";
    return { traces, layout };
  }

  // ----------------------------------------------------------------- stints
  function stints(spec, t) {
    const codes = spec.drivers.map((d) => d.code);
    const byCompound = {};
    for (const d of spec.drivers) {
      for (const s of d.stints) {
        (byCompound[s.compound] ||= []).push({ code: d.code, ...s });
      }
    }
    const order = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"];
    const compounds = Object.keys(byCompound).sort((a, b) => order.indexOf(a) - order.indexOf(b));
    const traces = compounds.map((c) => {
      const rows = byCompound[c];
      const fill = t.compound(c);
      return {
        type: "bar",
        orientation: "h",
        name: c.charAt(0) + c.slice(1).toLowerCase(),
        y: rows.map((r) => r.code),
        x: rows.map((r) => r.laps),
        base: rows.map((r) => r.start - 1),
        text: rows.map((r) => (r.laps >= 4 ? COMPOUND_LETTER[c] || "?" : "")),
        textposition: "inside",
        insidetextanchor: "middle",
        textfont: { color: inkOn(fill), size: 10 },
        customdata: rows.map((r) => [r.start, r.end, r.laps, r.age_start ?? "?"]),
        hovertemplate:
          `<b>%{y}</b> ${c.toLowerCase()}<br>Laps %{customdata[0]}–%{customdata[1]} (%{customdata[2]})` +
          `<br>Tyre age at fit: %{customdata[3]}<extra></extra>`,
        marker: { color: fill, line: { color: t.surface, width: 2 }, cornerradius: 4 },
      };
    });
    const layout = baseLayout(t, {
      barmode: "overlay",
      bargap: 0.28,
      hovermode: "closest",
      height: Math.max(220, codes.length * 24 + 80),
    });
    layout.yaxis.type = "category";
    layout.yaxis.categoryorder = "array";
    layout.yaxis.categoryarray = codes;
    layout.yaxis.autorange = "reversed";
    layout.yaxis.gridcolor = "rgba(0,0,0,0)";
    layout.yaxis.tickfont = { color: t.text2, size: 10 };
    layout.xaxis.title.text = "Lap";
    layout.xaxis.rangemode = "tozero";
    return { traces, layout };
  }

  // ---------------------------------------------------------------- weather
  function weather(spec, t) {
    const traces = [
      { name: "Track", y: spec.track, slot: 1 },
      { name: "Air", y: spec.air, slot: 2 },
    ].map((s) => ({
      type: "scatter",
      mode: "lines",
      x: spec.minute,
      y: s.y,
      name: s.name,
      line: { color: t.slot(s.slot), width: 2 },
      hovertemplate: `${s.name} %{y:.1f} °C<extra></extra>`,
    }));
    const shapes = [];
    let start = null;
    spec.rain.forEach((r, i) => {
      if (r && start === null) start = spec.minute[i];
      if ((!r || i === spec.rain.length - 1) && start !== null) {
        shapes.push({
          type: "rect",
          xref: "x",
          yref: "paper",
          x0: start,
          x1: spec.minute[i],
          y0: 0,
          y1: 1,
          fillcolor: alpha(t.axis, 0.18),
          line: { width: 0 },
          layer: "below",
        });
        start = null;
      }
    });
    const layout = baseLayout(t, { hovermode: "x unified", shapes });
    layout.xaxis.title.text = "Minutes from session start";
    layout.yaxis.title.text = "°C";
    return { traces, layout };
  }

  // -------------------------------------------------------------- telemetry
  const PANES = [
    { key: "speed", label: "Speed (km/h)", domain: [0.5, 1] },
    { key: "throttle", label: "Throttle %", domain: [0.33, 0.47] },
    { key: "brake", label: "Brake", domain: [0.25, 0.31] },
    { key: "gear", label: "Gear", domain: [0.13, 0.23] },
  ];

  function telemetry(spec, t) {
    const traces = [];
    const layout = baseLayout(t, {
      hovermode: "x unified",
      hoversubplots: "axis",
      height: 560,
      margin: { l: 8, r: 8, t: 30, b: 8 },
      annotations: [],
    });
    const axisBase = layout.yaxis;
    delete layout.yaxis;
    const panes = PANES.slice();
    if (spec.delta) panes.push({ key: "delta", label: "Gap (s)", domain: [0, 0.11] });
    panes.forEach((p, i) => {
      const name = i === 0 ? "yaxis" : `yaxis${i + 1}`;
      layout[name] = Object.assign({}, axisBase, {
        domain: p.domain,
        title: { text: "" },
        fixedrange: true,
        nticks: p.key === "speed" ? 6 : 3,
      });
      if (p.key === "brake") layout[name].showticklabels = false;
      layout.annotations.push({
        xref: "paper",
        yref: `${name.replace("axis", "")} domain`,
        x: 0,
        y: 1,
        xanchor: "left",
        yanchor: "bottom",
        text: p.label,
        showarrow: false,
        font: { size: 10, color: t.muted },
      });
    });
    for (const tr of spec.traces) {
      const colour = t.slot(tr.slot);
      PANES.forEach((p, i) => {
        traces.push({
          type: "scatter",
          mode: "lines",
          x: tr.distance,
          y: tr[p.key],
          yaxis: i === 0 ? "y" : `y${i + 1}`,
          name: tr.code,
          legendgroup: tr.code,
          showlegend: i === 0,
          line: { color: colour, width: p.key === "speed" ? 2 : 1.5, shape: p.key === "gear" || p.key === "brake" ? "hv" : "linear" },
          hovertemplate: `${tr.code} %{y}<extra></extra>`,
        });
      });
    }
    if (spec.delta) {
      const [a, b] = spec.traces;
      const yi = `y${panes.length}`;
      traces.push({
        type: "scatter",
        mode: "lines",
        x: spec.delta.distance,
        y: spec.delta.delta,
        yaxis: yi,
        name: `${b.code} vs ${a.code}`,
        showlegend: false,
        line: { color: t.text2, width: 2 },
        hovertemplate: `${b.code} vs ${a.code} %{y:+.3f}s<extra></extra>`,
      });
      const ax = layout[`yaxis${panes.length}`];
      ax.zeroline = true;
      ax.zerolinecolor = t.baseline;
      ax.zerolinewidth = 1;
      layout.annotations[layout.annotations.length - 1].text =
        `Gap (s): above 0 means ${b.code} is behind ${a.code}`;
    }
    layout.xaxis.title.text = "Distance (m)";
    layout.xaxis.anchor = `y${panes.length}`;
    layout.xaxis.showspikes = true;
    layout.xaxis.spikemode = "across";
    layout.xaxis.spikethickness = 1;
    layout.xaxis.spikecolor = t.baseline;
    layout.xaxis.spikedash = "solid";
    return { traces, layout };
  }

  // ------------------------------------------------------------------ track
  function track(spec, t) {
    const tr = spec.track;
    const traces = [];
    if (tr.dominance) {
      let i = 0;
      const seen = new Set();
      while (i < tr.x.length) {
        const owner = tr.dominance[i];
        let j = i;
        while (j < tr.x.length && tr.dominance[j] === owner) j++;
        const end = Math.min(j + 1, tr.x.length);
        const d = spec.drivers.find((x) => x.slot === owner);
        traces.push({
          type: "scatter",
          mode: "lines",
          x: tr.x.slice(i, end),
          y: tr.y.slice(i, end),
          line: { color: t.slot(owner), width: 5 },
          name: d ? `${d.code} faster` : "",
          legendgroup: String(owner),
          showlegend: !seen.has(owner),
          hoverinfo: "skip",
        });
        seen.add(owner);
        i = j;
      }
    } else {
      traces.push({
        type: "scatter",
        mode: "markers",
        x: tr.x,
        y: tr.y,
        marker: {
          color: tr.speed,
          colorscale: [
            [0, t.seqLow],
            [1, t.seqHigh],
          ],
          size: 5,
          colorbar: { title: { text: "km/h" }, thickness: 8, len: 0.6 },
        },
        hovertemplate: "%{marker.color} km/h<extra></extra>",
        showlegend: false,
      });
    }
    if (tr.corners.length) {
      traces.push({
        type: "scatter",
        mode: "text",
        x: tr.corners.map((c) => c.x),
        y: tr.corners.map((c) => c.y),
        text: tr.corners.map((c) => c.n),
        textfont: { size: 9, color: t.muted },
        hoverinfo: "skip",
        showlegend: false,
      });
    }
    const layout = baseLayout(t, { hovermode: false, margin: { l: 4, r: 4, t: 30, b: 4 }, height: 380 });
    for (const ax of ["xaxis", "yaxis"]) {
      Object.assign(layout[ax], { visible: false, fixedrange: true });
    }
    layout.yaxis.scaleanchor = "x";
    layout.yaxis.scaleratio = 1;
    return { traces, layout };
  }

  const RENDERERS = { lines, distribution, stints, weather, telemetry, track };

  // ------------------------------------------------------------- table view
  function el(tag, text) {
    const e = document.createElement(tag);
    if (text != null) e.textContent = String(text); // labels are untrusted data
    return e;
  }

  function buildTable(head, rows) {
    const table = el("table");
    const thead = el("thead");
    const tr = el("tr");
    head.forEach((h) => tr.appendChild(el("th", h)));
    thead.appendChild(tr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const r of rows) {
      const row = el("tr");
      r.forEach((c) => {
        const td = el("td", c ?? "");
        if (typeof c === "number") td.className = "num";
        row.appendChild(td);
      });
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    return table;
  }

  function tableFor(spec) {
    switch (spec.kind) {
      case "lines": {
        const series = spec.series;
        const xs = [...new Set(series.flatMap((s) => s.x))].sort((a, b) => a - b);
        const maps = series.map((s) => new Map(s.x.map((x, i) => [x, s.y[i]])));
        return buildTable(
          [spec.x_title || "x", ...series.map((s) => s.code)],
          xs.map((x) => [x, ...maps.map((m) => m.get(x))]),
        );
      }
      case "distribution":
        return buildTable(
          ["Driver", "Laps", "Best", "Median"],
          spec.series.map((s) => {
            const sorted = [...s.times].sort((a, b) => a - b);
            return [s.code, s.times.length, fmtLap(sorted[0]), fmtLap(sorted[Math.floor(sorted.length / 2)])];
          }),
        );
      case "stints":
        return buildTable(
          ["Driver", "Stints"],
          spec.drivers.map((d) => [
            d.code,
            d.stints.map((s) => `${COMPOUND_LETTER[s.compound] || "?"} L${s.start}–${s.end}`).join(" · "),
          ]),
        );
      case "weather":
        return buildTable(
          ["Minute", "Air °C", "Track °C", "Rain"],
          spec.minute.map((m, i) => [m, spec.air[i], spec.track[i], spec.rain[i] ? "yes" : ""]),
        );
      case "telemetry": {
        const [a, b] = spec.traces;
        const rows = [];
        const step = 100;
        for (let d = 0; d <= Math.max(...a.distance); d += step) {
          const ia = a.distance.findIndex((x) => x >= d);
          const ib = b ? b.distance.findIndex((x) => x >= d) : -1;
          rows.push([d, a.speed[ia], b && ib >= 0 ? b.speed[ib] : ""]);
        }
        return buildTable(["Distance (m)", `${a.code} km/h`, b ? `${b.code} km/h` : ""], rows);
      }
      case "track":
        return buildTable(
          ["Corner"],
          spec.track.corners.map((c) => [c.n]),
        );
      default:
        return el("p", "No table view");
    }
  }

  // ----------------------------------------------------------------- render
  function render(fig) {
    const dataEl = fig.querySelector("script.chart-data");
    const plot = fig.querySelector(".plot");
    if (!dataEl || !plot || !window.Plotly) return;
    const spec = JSON.parse(dataEl.textContent);
    const fn = RENDERERS[spec.kind];
    if (!fn) return;
    const { traces, layout } = fn(spec, tokens());
    if (layout.height) plot.style.minHeight = `${layout.height}px`;
    Plotly.react(plot, traces, layout, CONFIG);
    const details = fig.querySelector("details");
    if (details && !details.dataset.wired) {
      details.dataset.wired = "1";
      details.addEventListener("toggle", () => {
        const wrap = details.querySelector(".table-wrap");
        if (details.open && !wrap.firstChild) wrap.appendChild(tableFor(spec));
      });
    }
  }

  function renderAll(root) {
    (root || document).querySelectorAll("figure[data-chart]").forEach(render);
  }

  document.addEventListener("htmx:load", (ev) => {
    const root = ev.detail.elt;
    if (root.matches && root.matches("figure[data-chart]")) render(root);
    else renderAll(root);
  });
  document.addEventListener("DOMContentLoaded", () => renderAll(document));
  document.addEventListener("themechange", () => renderAll(document));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderAll(document));
})();
