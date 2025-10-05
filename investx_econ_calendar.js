/* InvestX Economic Calendar — estilo Investing (texto) sobre feed JSON de ForexFactory
   Requiere env: INVESTX_TOKEN, CHAT_ID
   Opcional:
     - VERIFY_TELEGRAM=1           (ping de prueba)
     - SKIP_PNG=1                  (recomendado; evita imagen)
     - STYLE=investing|compact     (por defecto 'investing')
     - COUNTRY=USD                 (p.ej. USD, EUR, GBP… separados por coma si varios)
     - IMPACT_MIN=medium|high      (nivel mínimo de impacto; por defecto 'medium')
*/

const fs = require('fs');
const path = require('path');

/* ====== Polyfills HTTP (fetch/FormData/Blob/AbortController) vía undici si faltan ====== */
let _fetch = global.fetch;
let _FormData = global.FormData;
let _Blob = global.Blob;
let _AbortController = global.AbortController;

async function ensureHTTPPolyfills(){
  if (_fetch && _FormData && _Blob && _AbortController) return;
  try {
    const undici = await import('undici');
    _fetch = _fetch || undici.fetch;
    _FormData = _FormData || undici.FormData;
    _Blob = _Blob || undici.Blob;
    _AbortController = _AbortController || undici.AbortController;
    console.log('[bootstrap] undici activo (fetch/FormData/Blob/AbortController)');
  } catch (e) {
    console.error('Falta fetch/FormData/Blob y no se pudo cargar undici. Instala undici o usa Node >= 18.');
    process.exit(1);
  }
}

/* ================== util: zona horaria y formatos ================== */
const TZ = 'Europe/Madrid';
const NOW = () => {
  const d = new Date();
  const p = Object.fromEntries(new Intl.DateTimeFormat('sv-SE', {
    timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'
  }).formatToParts(d).map(x=>[x.type,x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
};

const fmtDateISO = d => new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle: 'short' }).format(d); // yyyy-mm-dd
const fmtDateES  = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, day:'2-digit', month:'2-digit', year:'numeric' }).format(d); // dd/mm/aaaa
const fmtTime    = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
const weekdayES  = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, weekday:'long' }).format(d);
const isMonday   = () => new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday:'short' }).format(new Date()).toLowerCase()==='mon';
const weekRangeES = () => {
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff = wd===0 ? -6 : 1-wd;
  const mon = new Date(d); mon.setDate(d.getDate()+diff);
  const sun = new Date(mon); sun.setDate(mon.getDate()+6);
  return { monday: fmtDateES(mon), sunday: fmtDateES(sun) };
};

/* ================== helper: timeout promisificado ================== */
function withTimeout(promise, ms, label='op') {
  let t;
  const timeout = new Promise((_, rej) => {
    t = setTimeout(() => rej(new Error(`timeout ${label} ${ms}ms`)), ms);
  });
  return Promise.race([promise.finally(() => clearTimeout(t)), timeout]);
}

/* ================== fetch con timeout + reintentos genérico ================== */
async function fetchWithTimeout(url, {
  timeoutMs = 15000,
  retries = 2,
  retryDelayBaseMs = 800,
  method = 'GET',
  headers = {},
  body = undefined,
} = {}) {
  await ensureHTTPPolyfills();
  let lastErr;
  for (let i = 0; i <= retries; i++) {
    const ctrl = new _AbortController();
    const timer = setTimeout(() => ctrl.abort(new Error('Timeout')), timeoutMs);
    try {
      const res = await _fetch(url, { method, headers, body, signal: ctrl.signal });
      clearTimeout(timer);
      if (!res.ok) {
        let txt = '';
        try { txt = await res.text(); } catch(_){}
        throw new Error(`HTTP ${res.status}${txt ? ` — ${txt.slice(0,200)}`:''}`);
      }
      return res;
    } catch (e) {
      clearTimeout(timer);
      lastErr = e;
      if (i < retries) {
        const delay = retryDelayBaseMs * (i + 1);
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }
  throw lastErr || new Error('fetch failed');
}

/* ================== Datos: ForexFactory JSON (semana) ================== */
async function fetchFFWeek() {
  const url = `https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`;
  const res = await fetchWithTimeout(url, {
    timeoutMs: 15000,
    retries: 2,
    headers: { 'User-Agent':'Mozilla/5.0', 'Accept':'application/json' }
  });
  return res.json();
}

/* ========= Normalización + filtros (país/impacto) ========= */
function filterEvents(raw, onlyToday) {
  const todayStr = fmtDateISO(new Date()); // yyyy-mm-dd en TZ
  const countries = (process.env.COUNTRY || 'USD')
    .split(',')
    .map(s => s.trim().toUpperCase())
    .filter(Boolean);
  const impactMin = (process.env.IMPACT_MIN || 'medium').toLowerCase();

  const impactRank = v => /high/i.test(v) ? 2 : /medium/i.test(v) ? 1 : 0;
  const minRank = impactMin === 'high' ? 2 : 1;

  return raw
    .filter(e => countries.includes((e.country||'').toUpperCase()))
    .filter(e => impactRank(e.impact||'') >= minRank)
    .map(e => {
      const ts = (Number(e.timestamp)||0)*1000;
      const dt = ts ? new Date(ts) : new Date();
      return {
        dateISO: fmtDateISO(dt),  // yyyy-mm-dd
        dateES:  fmtDateES(dt),   // dd/mm/aaaa
        time: fmtTime(dt),
        weekday: weekdayES(dt),
        title: (e.title||'').trim(),
        forecast: (e.forecast??'').toString().trim(),  // Prev.
        previous: (e.previous??'').toString().trim(),  // Anterior
        impact: (e.impact||'').toLowerCase()
      };
    })
    .filter(e => onlyToday ? e.dateISO===todayStr : true)
    .sort((a,b)=>(a.dateISO+a.time).localeCompare(b.dateISO+b.time));
}

/* ================== Render estilo Investing (texto HTML con ⭐️) ================== */
function impactToStars(impact){
  if (/high/.test(impact)) return '⭐️⭐️⭐️';
  return '⭐️⭐️'; // medium
}

function buildInvestingStyle(events, weekly){
  const tz = 'Europe/Madrid';
  if(!events.length){
    if (weekly) {
      const {monday, sunday} = weekRangeES();
      return `<b>🗓️ Calendario Económico (🇺🇸) — Semana ${monday}–${sunday} (${tz})</b>\n<i>No hay eventos de EE. UU. con el filtro actual.</i>`;
    }
    return `<b>🗓️ Calendario Económico (🇺🇸) — Hoy ${fmtDateES(new Date())} (${tz})</b>\n<i>Hoy no hay eventos de EE. UU.</i>`;
  }

  // agrupar por día (ordenado)
  const byDay = new Map();
  for (const e of events){
    const key = `${e.weekday} ${e.dateES}`; // "martes 07/10/2025"
    if(!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(e);
  }

  // cabecera
  let header;
  if (weekly) {
    const {monday, sunday} = weekRangeES();
    header = `<b>🗓️ Calendario Económico (🇺🇸) — Semana ${monday}–${sunday} (${tz})</b>\n<i>Impacto: ⭐️⭐️ / ⭐️⭐️⭐️</i>`;
  } else {
    const today = new Date();
    header = `<b>🗓️ Calendario Económico (🇺🇸) — Hoy ${fmtDateES(today)} (${tz})</b>\n<i>Impacto: ⭐️⭐️ / ⭐️⭐️⭐️</i>`;
  }

  const lines = [header, ''];
  for (const [day, items] of byDay){
    lines.push(`<b>${capitalize(day)}</b>`);
    for (const ev of items){
      const stars = impactToStars(ev.impact);
      const titleIcon = decorateTitle(ev.title);
      // Línea 1
      lines.push(`• <b>${ev.time} — ${stars} — ${titleIcon}</b>`);
      // Línea 2 (Actual | Prev. | Anterior)
      const fields = [];
      // FF antes de la publicación no trae “Actual”. Mostramos guion para mantener la estética.
      fields.push(`Actual: —`);
      if (ev.forecast)  fields.push(`Prev.: ${escapeTxt(ev.forecast)}`);
      else              fields.push(`Prev.: —`);
      if (ev.previous)  fields.push(`Anterior: ${escapeTxt(ev.previous)}`);
      else              fields.push(`Anterior: —`);
      lines.push(`<i>${fields.join(' | ')}</i>`);
    }
    lines.push('');
  }

  // control de longitud (Telegram ~4096)
  let html = lines.join('\n');
  const LIMIT = 3900;
  if (html.length > LIMIT){
    html = html.slice(0, LIMIT-40) + '\n<i>…recortado por longitud</i>';
  }
  return html.trim();
}

function capitalize(s){ return s.charAt(0).toUpperCase() + s.slice(1); }
function decorateTitle(t){
  const T = t.toLowerCase();
  if (/(cpi|inflation|ipc|pce)/.test(T)) return '📊 ' + escapeTxt(t);
  if (/(non-?farm|nfp|payroll)/.test(T)) return '📊 ' + escapeTxt(t);
  if (/(fomc|powell|fed chair|fed speaks|minutes|remarks)/.test(T)) return '🗣️ ' + escapeTxt(t);
  return escapeTxt(t);
}
function escapeTxt(s){
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ================== Telegram helpers (con reintentos) ================== */
async function sendTelegramText(token, chatId, html){
  await ensureHTTPPolyfills();
  const url=`https://api.telegram.org/bot${token}/sendMessage`;
  const body = new URLSearchParams({
    chat_id: chatId,
    text: html,
    parse_mode: 'HTML',
    disable_web_page_preview: 'true'
  });
  const res = await fetchWithTimeout(url, {
    method: 'POST',
    body,
    timeoutMs: 15000,
    retries: 2,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  });
  const json = await res.json();
  if (!json.ok) throw new Error(`sendMessage Telegram error: ${JSON.stringify(json)}`);
  console.log('Telegram text OK');
}

async function verifyTelegram(token, chatId){
  if(!process.env.VERIFY_TELEGRAM) return;
  console.log('VERIFY_TELEGRAM=1 → ping de prueba…');
  await sendTelegramText(token, chatId, '✅ InvestX cron conectado (ping).');
}

/* ================== Main ================== */
(async ()=>{
  // watchdog global: 3 min
  const watchdog = setTimeout(()=>{ 
    console.error('Watchdog: timeout global alcanzado, salgo.'); 
    process.exit(1); 
  }, 3*60*1000);

  const token=process.env.INVESTX_TOKEN, chatId=process.env.CHAT_ID;
  if(!token||!chatId){ 
    console.error('Faltan INVESTX_TOKEN / CHAT_ID'); 
    clearTimeout(watchdog); 
    process.exit(1); 
  }

  const style = (process.env.STYLE||'investing').toLowerCase();
  const weekly=isMonday();

  console.log(`[${NOW()}] Start. CHAT_ID=${chatId} STYLE=${style} weekly=${weekly}`);
  await verifyTelegram(token, chatId);

  console.log('Descargando feed semanal (FF)…');
  const raw=await fetchFFWeek();
  console.log(`Items recibidos: ${raw.length}`);

  const events=filterEvents(raw, !weekly);
  console.log(`Filtrados ${process.env.COUNTRY||'USD'} + impacto >= ${process.env.IMPACT_MIN||'medium'}: ${events.length} (onlyToday=${!weekly})`);

  // Render texto (investing por defecto)
  let html = buildInvestingStyle(events, weekly);
  if(!html){
    const {monday,sunday} = weekRangeES();
    const caption = weekly
      ? `🗓️ Calendario USA — Semana ${monday}–${sunday} (${TZ})`
      : `🗓️ Calendario USA — Hoy ${fmtDateES(new Date())} (${TZ})`;
    html = `${caption}\n\n(No hay eventos relevantes).`;
  }

  console.log('Enviando texto…');
  await sendTelegramText(token, chatId, html);

  console.log('OK fin cron.');
  clearTimeout(watchdog);
})().catch(err=>{ 
  console.error('ERROR:', err && err.stack ? err.stack : err); 
  process.exit(1); 
});
