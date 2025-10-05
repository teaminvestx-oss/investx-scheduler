/*
==========================================================
🗓️ InvestX Economic Calendar — ForexFactory Parser + DeepL
Formato texto para Telegram (🇺🇸 eventos, medio/alto impacto)
==========================================================
*/

console.log("Node runtime:", process.version);

const axios = require("axios");
const { DateTime } = require("luxon");

// --- CONFIGURACIÓN ---------------------------------------
const TG_TOKEN = process.env.BOT_TOKEN;
const TG_CHAT_ID = process.env.CHAT_ID;

const DEEPL_KEY = process.env.DEEPL_API_KEY || "";
const DEEPL_PLAN = process.env.DEEPL_PLAN || "free"; // "free" o "pro"

const FORCE_DATE_FROM = process.env.FORCE_DATE_FROM || "";
const FORCE_DATE_TO = process.env.FORCE_DATE_TO || "";
const VERBOSE = parseInt(process.env.VERBOSE || "0");

const TIMEZONE = process.env.TZ || "Europe/Madrid";

const MIN_IMPACT = "medium"; // filtramos medium+high
const COUNTRY_FILTER = "USD";

// ==========================================================

const FEED_URL =
  "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json";
const DEEPL_URL =
  DEEPL_PLAN === "pro"
    ? "https://api.deepl.com/v2/translate"
    : "https://api-free.deepl.com/v2/translate";

// ----------------------------------------------------------

async function deeplTranslate(text, targetLang = "es") {
  if (!DEEPL_KEY) return text;
  try {
    const res = await axios.post(
      DEEPL_URL,
      new URLSearchParams({
        auth_key: DEEPL_KEY,
        text,
        target_lang: targetLang.toUpperCase(),
      }).toString(),
      { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
    );
    return res.data?.translations?.[0]?.text || text;
  } catch (err) {
    console.error("DeepL error:", err.message);
    return text;
  }
}

// Heurística para eventos típicos a y media hora
function isTypicalUSAtHalf(title) {
  const s = (title || "").toLowerCase();
  return /unemployment claims|jobless claims|continuing|non-?farm|payroll|nfp|unemployment rate|average hourly earnings|cpi|consumer price|pce|core pce|retail sales|ppi|producer price/.test(
    s
  );
}

function normalizeTime(event) {
  let timeUTC = event.date;
  let t = DateTime.fromISO(timeUTC, { zone: "UTC" }).setZone(TIMEZONE);
  if (isTypicalUSAtHalf(event.title)) {
    // Ajuste exacto a y media
    t = t.set({ minute: 30, second: 0 });
  }
  return t;
}

function impactStars(impact) {
  if (/high/i.test(impact)) return "⭐⭐⭐";
  if (/medium/i.test(impact)) return "⭐⭐";
  return "⭐";
}

async function getForexFactoryData() {
  try {
    const { data } = await axios.get(FEED_URL, {
      headers: { "Cache-Control": "no-cache" },
    });
    return data;
  } catch (err) {
    console.error("Error al obtener feed FF:", err.message);
    return [];
  }
}

// ----------------------------------------------------------

async function main() {
  const from = FORCE_DATE_FROM
    ? DateTime.fromISO(FORCE_DATE_FROM)
    : DateTime.now().setZone(TIMEZONE).startOf("week").plus({ days: 1 });
  const to = FORCE_DATE_TO
    ? DateTime.fromISO(FORCE_DATE_TO)
    : from.plus({ days: 6 });

  console.log("📅 Rango:", from.toISODate(), "→", to.toISODate());

  const events = await getForexFactoryData();

  // Filtrado base
  const filtered = events.filter((e) => {
    if (e.country !== COUNTRY_FILTER) return false;
    if (!/medium|high/i.test(e.impact)) return false;
    const d = DateTime.fromISO(e.date, { zone: "UTC" });
    return d >= from && d <= to;
  });

  if (!filtered.length) {
    await sendTelegram(
      `🗓️ *Calendario Económico (🇺🇸)* — Rango ${from.toISODate()}–${to.toISODate()} (${TIMEZONE})\nImpacto: ⭐⭐ (medio) · ⭐⭐⭐ (alto)\n\nNo hay eventos de EE. UU. con el filtro actual.`
    );
    console.log("Sin eventos filtrados.");
    return;
  }

  // Agrupamos por día
  const byDay = {};
  for (const e of filtered) {
    const t = normalizeTime(e);
    const day = t.toFormat("cccc dd/LL/yyyy");
    if (!byDay[day]) byDay[day] = [];
    byDay[day].push({ ...e, time: t });
  }

  let text =
    `🗓️ *Calendario Económico (🇺🇸)* — Rango ${from.toISODate()}–${to.toISODate()} (${TIMEZONE})\nImpacto: ⭐⭐ (medio) · ⭐⭐⭐ (alto)\n\n`;

  for (const day of Object.keys(byDay)) {
    text += `*${day}*\n`;
    const items = byDay[day].sort((a, b) => a.time - b.time);

    for (const e of items) {
      const hour = e.time.toFormat("HH:mm");
      const stars = impactStars(e.impact);
      let titleES = (await deeplTranslate(e.title || "", "es")).trim();

      // Diferenciar “Continuing Claims”
      if (/(continuing)/i.test(e.title || "")) {
        if (!/continu/i.test(titleES) && !/continuad/i.test(titleES)) {
          titleES += " (continuadas)";
        }
      }

      text += `• ${hour} — ${stars} — ${titleES}\n`;
    }

    text += `\n`;
  }

  await sendTelegram(text.trim());
  console.log("✅ Telegram enviado con", filtered.length, "eventos.");
}

// ----------------------------------------------------------

async function sendTelegram(msg) {
  if (!TG_TOKEN || !TG_CHAT_ID) {
    console.log("Sin credenciales Telegram, skip envío");
    console.log(msg);
    return;
  }
  try {
    const url = `https://api.telegram.org/bot${TG_TOKEN}/sendMessage`;
    await axios.post(url, {
      chat_id: TG_CHAT_ID,
      text: msg,
      parse_mode: "Markdown",
    });
  } catch (err) {
    console.error("Error Telegram:", err.message);
  }
}

// ----------------------------------------------------------
main();
