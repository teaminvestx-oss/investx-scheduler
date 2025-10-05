/* =========================================================================
   📅 InvestX Economic Calendar (ForexFactory JSON)
   Texto limpio · Filtros USD + impacto medio/alto
   · Compatible con Render cron
   ========================================================================= */

const fs = require("fs");
const fetch = (...args) =>
  import("node-fetch").then(({ default: fetch }) => fetch(...args));

const TZ = "Europe/Madrid";
const VERBOSE = process.env.VERBOSE === "1" || process.env.VERBOSE === "true";

// ===== utilidades base =====
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmtDateISO = (d) => d.toISOString().split("T")[0];
const fmtDateES = (d) =>
  new Intl.DateTimeFormat("es-ES", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: TZ,
  }).format(d);
const fmtTime = (d) =>
  new Intl.DateTimeFormat("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: TZ,
  }).format(d);

function isMonday() {
  return (
    new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "short" })
      .format(new Date())
      .toLowerCase() === "mon"
  );
}

// ========== FETCH CON TIMEOUT ==========
async function fetchWithTimeout(url, { timeoutMs = 15000, retries = 2 } = {}) {
  let err;
  for (let i = 0; i <= retries; i++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      clearTimeout(timer);
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res;
    } catch (e) {
      clearTimeout(timer);
      err = e;
      await sleep(800 * (i + 1));
    }
  }
  throw err;
}

// ========== DESCARGA DE FEEDS ==========
async function fetchFFWeek() {
  const url = `https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`;
  const res = await fetchWithTimeout(url, { timeoutMs: 20000 });
  return res.json();
}
async function fetchFFNextWeek() {
  const url = `https://nfs.faireconomy.media/ff_calendar_nextweek.json?_=${Date.now()}`;
  const res = await fetchWithTimeout(url, { timeoutMs: 20000 });
  return res.json();
}

function weekMonday(dateISO) {
  const d = new Date(dateISO + "T00:00:00");
  const wd = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"].indexOf(
    new Intl.DateTimeFormat("en-US", {
      timeZone: TZ,
      weekday: "short",
    })
      .format(d)
      .toLowerCase()
  );
  const diff = wd === 0 ? -6 : 1 - wd;
  const mon = new Date(d);
  mon.setDate(d.getDate() + diff);
  return fmtDateISO(mon);
}

// ========== PROCESAR EVENTOS ==========
function buildEventsFromFF(raw, { fromISO, toISO, impactMin }) {
  const impacts = impactMin === "high" ? ["high"] : ["medium", "high"];
  const start = new Date(fromISO + "T00:00:00");
  const end = new Date(toISO + "T23:59:59");

  const filtered = raw
    .filter((e) => {
      const cc = (e.country || e.countryCode || e.currency || "").toUpperCase();
      if (!(cc.includes("US") || cc.includes("USD"))) return false;
      const impact = (e.impact || "").toLowerCase();
      if (!impacts.some((lvl) => impact.includes(lvl))) return false;
      const ts = Number(e.timestamp) * 1000 || 0;
      const dt = ts ? new Date(ts) : null;
      return dt && dt >= start && dt <= end;
    })
    .map((e) => {
      const dt = new Date(Number(e.timestamp) * 1000);
      return {
        title: e.title?.trim() || "—",
        time: fmtTime(dt),
        dayLabel: fmtDateES(dt),
        stars: e.impact?.toLowerCase().includes("high")
          ? "⭐️⭐️⭐️"
          : "⭐️⭐️",
      };
    });

  return filtered;
}

// ========== FORMATO TELEGRAM ==========
function limitTelegram(s) {
  return s.length > 3900 ? s.slice(0, 3800) + "\n…" : s;
}

function buildWeeklyMessageWithCustomHeader(events, rangeLabelES) {
  const head = `🗓️ <b>Calendario Económico (🇺🇸)</b> — Rango ${rangeLabelES} (${TZ})\nImpacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)\n\n`;
  if (!events.length)
    return `${head}No hay eventos de EE. UU. con el filtro actual.`;
  const map = new Map();
  for (const e of events) {
    if (!map.has(e.dayLabel)) map.set(e.dayLabel, []);
    map.get(e.dayLabel).push(e);
  }
  const lines = [head];
  for (const [day, arr] of map) {
    lines.push(`<b>${day}</b>`);
    const MAX = 5;
    for (const ev of arr.slice(0, MAX))
      lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
    if (arr.length > MAX)
      lines.push(`  +${arr.length - MAX} más…`);
    lines.push("");
  }
  return limitTelegram(lines.join("\n").trim());
}

// ========== TELEGRAM ==========
async function sendTelegramText(token, chatId, html) {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const body = new URLSearchParams({
    chat_id: chatId,
    text: html,
    parse_mode: "HTML",
    disable_web_page_preview: "true",
  });
  const res = await fetch(url, { method: "POST", body });
  if (!res.ok) throw new Error("Telegram send failed " + res.status);
}

// ========== MAIN ==========
(async () => {
  const token = process.env.INVESTX_TOKEN;
  const chatId = process.env.CHAT_ID;
  if (!token || !chatId) throw new Error("Faltan INVESTX_TOKEN / CHAT_ID");

  console.log("CFG:", {
    forceFrom: process.env.FORCE_DATE_FROM,
    forceTo: process.env.FORCE_DATE_TO,
    impact: process.env.IMPACT_MIN || "medium",
    tz: TZ,
    verbose: VERBOSE,
  });

  const weekly = isMonday();
  let fromISO, toISO, headerRangeLabel = null;

  if (process.env.FORCE_DATE_FROM && process.env.FORCE_DATE_TO) {
    fromISO = process.env.FORCE_DATE_FROM.trim();
    toISO = process.env.FORCE_DATE_TO.trim();
    headerRangeLabel = `${fmtDateES(new Date(fromISO + "T00:00:00"))}–${fmtDateES(
      new Date(toISO + "T00:00:00")
    )}`;
    console.log(`🧰 Prueba ACTIVADA: ${fromISO}→${toISO}`);
  } else if (weekly) {
    const now = new Date();
    const monday = new Date(now);
    monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    fromISO = fmtDateISO(monday);
    toISO = fmtDateISO(sunday);
  } else {
    const d = new Date();
    fromISO = fmtDateISO(d);
    toISO = fmtDateISO(d);
  }

  // Descarga JSON (thisweek + nextweek)
  let raw = [];
  const thisW = await fetchFFWeek();
  raw = raw.concat(thisW || []);
  const thisMonISO = weekMonday(fmtDateISO(new Date()));
  const forceMonISO = weekMonday(fromISO);
  const oneWeekAhead = (a, b) => {
    const diff = (new Date(a) - new Date(b)) / 86400000;
    return diff >= 6 && diff <= 8;
  };
  if (process.env.FORCE_DATE_FROM && oneWeekAhead(forceMonISO, thisMonISO)) {
    try {
      const nextW = await fetchFFNextWeek();
      raw = raw.concat(nextW || []);
      console.log(`FF nextweek añadido: ${nextW?.length || 0} items`);
    } catch (e) {
      console.warn("Aviso: no pude cargar nextweek:", e.message);
    }
  }

  console.log("Total items raw (this+next):", raw.length);

  // ==== DEBUG: muestra primeros elementos ====
  if (VERBOSE && raw.length) {
    console.log("--- Primeros elementos del feed ForexFactory ---");
    for (const e of raw.slice(0, 5)) {
      console.log({
        id: e.id || e.newsId || e.newsid,
        title: e.title,
        country: e.country,
        currency: e.currency,
        impact: e.impact,
        timestamp: e.timestamp,
        date: e.date,
      });
    }
    console.log("-----------------------------------------------");
  }

  const events = buildEventsFromFF(raw, {
    fromISO,
    toISO,
    impactMin: (process.env.IMPACT_MIN || "medium").toLowerCase(),
  });

  console.log("Eventos FF dentro de rango:", events.length);
  if (VERBOSE) console.log("sample events:", events.slice(0, 3));

  const msg = buildWeeklyMessageWithCustomHeader(events, headerRangeLabel);
  await sendTelegramText(token, chatId, msg);
  console.log("Telegram OK · Fin");
})();
