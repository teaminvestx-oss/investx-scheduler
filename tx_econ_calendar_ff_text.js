/*
====================================================
📅 InvestX Economic Calendar (🇺🇸)
Versión: 2025-10-05 · CommonJS (.cjs sin imports)
====================================================
- Frecuencia: semanal (lunes) / diaria (mar-vie)
- Rango forzado: FORCE_DATE_FROM / FORCE_DATE_TO
- Filtro: Impacto medio/alto + USD
- Zona horaria: Europe/Madrid
- SHOW_DESC=1 activa descripciones breves
====================================================
*/

console.log('Node runtime:', process.version);

// ========= CONFIG =========
const axios = require('axios');
const dayjs = require('dayjs');
const utc = require('dayjs/plugin/utc');
const tz = require('dayjs/plugin/timezone');
dayjs.extend(utc);
dayjs.extend(tz);

const BOT_TOKEN = process.env.INVESTX_TOKEN;
const CHAT_ID = process.env.CHAT_ID;

const FORCE_DATE_FROM = process.env.FORCE_DATE_FROM || null;
const FORCE_DATE_TO = process.env.FORCE_DATE_TO || null;
const TZ = process.env.TZ || 'Europe/Madrid';
const IMPACT_MIN = process.env.IMPACT_MIN || 'medium';
const VERBOSE = (process.env.VERBOSE || '').toString().toLowerCase() === 'true';
const SHOW_DESC = (process.env.SHOW_DESC || '').toString().toLowerCase() === '1';

// ========= HELPERS =========
const sleep = (ms) => new Promise(res => setTimeout(res, ms));

function weekdayES(dt) {
  const days = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
  return days[dt.getDay()];
}

function fmtDateES(dt) {
  return dt.toISOString().split('T')[0].split('-').reverse().join('/');
}

// --- Heurística eventos típicos USA ---
function isTypicalUSAtHalf(title) {
  const s = (title || '').toLowerCase();
  return (
    /unemployment claims|jobless claims|non-?farm|payroll|nfp|cpi|consumer price|pce|retail sales|core pce|producer price|ppi/.test(s)
  );
}

// --- Formateador hora (redondea y fija :30 si aplica) ---
function fmtTimeUS(dt, title) {
  const snapped = new Date(Math.round(dt.getTime() / 60000) * 60000);
  let parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false
    }).formatToParts(snapped).map(x => [x.type, x.value])
  );
  let hh = parseInt(parts.hour, 10);
  let mm = parseInt(parts.minute, 10);
  if (isTypicalUSAtHalf(title)) mm = 30;
  const HH = String(hh).padStart(2, '0');
  const MM = String(mm).padStart(2, '0');
  return `${HH}:${MM}`;
}

// --- Descripción breve ---
function shortDescES(title) {
  const s = (title || '').toLowerCase();
  if (/unemployment claims|jobless claims/.test(s))
    return 'Solicitudes semanales de paro (indicador de ciclo).';
  if (/continuing jobless/.test(s))
    return 'Demandantes de paro continuados (presión sobre mercado laboral).';
  if (/non-?farm|payroll|nfp/.test(s))
    return 'Empleo no agrícola: referencia mensual clave del mercado laboral.';
  if (/unemployment rate/.test(s))
    return 'Porcentaje de parados vs fuerza laboral.';
  if (/average hourly earnings/.test(s))
    return 'Crecimiento salarial (tensión inflacionaria).';
  if (/consumer price|cpi/.test(s))
    return 'Inflación IPC (precios al consumo).';
  if (/pce/.test(s))
    return 'Inflación PCE (indicador favorito de la Fed).';
  if (/retail sales/.test(s))
    return 'Gasto del consumidor (motor del PIB).';
  if (/fomc.*minutes/.test(s))
    return 'Acta de la reunión de la Fed; pistas de orientación futura.';
  if (/powell|fed chair.*speaks|remarks/.test(s))
    return 'Comentarios de Powell con impacto potencial en expectativas.';
  return null;
}

// --- Impacto ---
function impactToStars(impact) {
  const s = (impact || '').toLowerCase();
  if (s === 'high') return '⭐⭐⭐';
  if (s === 'medium') return '⭐⭐';
  return '⭐';
}

// --- Traducción de títulos básicos ---
function translateTitleES(t) {
  return t
    .replace(/Unemployment Claims/gi, 'Peticiones de subsidio por desempleo')
    .replace(/Nonfarm Payrolls/gi, 'Empleo no agrícola (NFP)')
    .replace(/Unemployment Rate/gi, 'Tasa de desempleo')
    .replace(/FOMC Minutes/gi, 'Minutas del FOMC')
    .replace(/Average Hourly Earnings/gi, 'Salario medio por hora m/m')
    .replace(/Powell Speaks/gi, 'Discurso de Powell (Fed)')
    .replace(/CPI/gi, 'IPC')
    .replace(/PCE/gi, 'PCE')
    .replace(/Retail Sales/gi, 'Ventas minoristas')
    .replace(/FOMC Statement/gi, 'Declaración FOMC');
}

// --- Decorador ---
function decorateTitleES(t) {
  const s = t.toLowerCase();
  if (/powell|fed/.test(s)) return '🗣 ' + t;
  if (/payroll|nfp|unemployment|employment/.test(s)) return '📊 ' + t;
  if (/price|inflation|cpi|pce/.test(s)) return '💰 ' + t;
  if (/retail|sales/.test(s)) return '🛒 ' + t;
  return t;
}

// ========= FETCH =========
async function fetchForexFactory(dateFrom, dateTo) {
  const url = `https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json`;
  const res = await axios.get(url, { timeout: 15000 });
  return res.data || [];
}

// ========= BUILD EVENTS =========
async function buildEvents() {
  const from = FORCE_DATE_FROM || dayjs().tz(TZ).startOf('week').add(1, 'day').format('YYYY-MM-DD');
  const to = FORCE_DATE_TO || dayjs(from).add(4, 'day').format('YYYY-MM-DD');
  console.log('🧰 Prueba ACTIVADA:', from + '→' + to);

  const data = await fetchForexFactory(from, to);
  if (!Array.isArray(data)) {
    console.log('⚠️ Datos vacíos');
    return [];
  }

  let out = [];
  for (const e of data) {
    if (!e || !e.title || !e.country) continue;
    if (e.country !== 'USD') continue;
    if (!['medium', 'high'].includes(e.impact?.toLowerCase?.() || '')) continue;

    const dt = new Date(e.date);
    const dayKey = dayjs(dt).tz(TZ).format('YYYY-MM-DD');
    out.push({
      dayKey,
      dayLabel: `${weekdayES(dt)} ${fmtDateES(dt)}`,
      time: fmtTimeUS(dt, e.title || ''),
      stars: impactToStars(e.impact),
      title: decorateTitleES(translateTitleES(e.title || '')),
      desc: SHOW_DESC ? shortDescES(e.title || '') : null
    });
  }

  return out.sort((a, b) => a.dayKey.localeCompare(b.dayKey) || a.time.localeCompare(b.time));
}

// ========= BUILD MESSAGE =========
function buildMessage(events, from, to) {
  if (!events.length) {
    return `📅 *Calendario Económico (🇺🇸)* —\nRango ${from}→${to}\n(Europe/Madrid)\n\nNo hay eventos de EE. UU. con el filtro actual.`;
  }

  let msg = `📅 *Calendario Económico (🇺🇸)* —\nRango ${from}→${to}\n(Europe/Madrid)\nImpacto: ⭐⭐ (medio) · ⭐⭐⭐ (alto)\n\n`;
  const grouped = events.reduce((acc, ev) => {
    (acc[ev.dayLabel] = acc[ev.dayLabel] || []).push(ev);
    return acc;
  }, {});

  for (const [day, arr] of Object.entries(grouped)) {
    msg += `*${day}*\n`;
    for (const ev of arr) {
      msg += `• ${ev.time} — ${ev.stars} — ${ev.title}\n`;
      if (ev.desc) msg += `  · ${ev.desc}\n`;
    }
    msg += `\n`;
  }

  return msg.trim();
}

// ========= TELEGRAM =========
async function sendTelegram(msg) {
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
  await axios.post(url, {
    chat_id: CHAT_ID,
    text: msg,
    parse_mode: 'Markdown'
  });
  console.log('📨 Telegram OK · Fin');
}

// ========= MAIN =========
(async () => {
  try {
    const from = FORCE_DATE_FROM || dayjs().tz(TZ).startOf('week').add(1, 'day').format('YYYY-MM-DD');
    const to = FORCE_DATE_TO || dayjs(from).add(4, 'day').format('YYYY-MM-DD');
    const events = await buildEvents();
    const msg = buildMessage(events, from, to);
    await sendTelegram(msg);
  } catch (err) {
    console.error('❌ Error:', err.message);
  }
})();
