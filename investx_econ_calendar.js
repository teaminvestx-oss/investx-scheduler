/* InvestX Economic Calendar — FUENTE: Investing (widget público)
   Formato: hora — ⭐️ — evento (ES), agrupado por día
   Lunes => semanal | Mar–Vie => diario | Fines de semana => opcionalmente no ejecuta
   Requiere env: INVESTX_TOKEN, CHAT_ID
   Opcional:
     TZ=Europe/Madrid
     IMPACT_MIN=medium|high
     BLOCK_WEEKENDS=1
*/

let _fetch = global.fetch, _FormData = global.FormData, _AbortController = global.AbortController;

async function ensureHTTP(){
  if (_fetch && _FormData && _AbortController) return;
  try {
    const undici = await import('undici');
    _fetch = _fetch || undici.fetch;
    _FormData = _FormData || undici.FormData;
    _AbortController = _AbortController || undici.AbortController;
  } catch {
    console.error('No hay fetch nativo y no se pudo cargar undici. Usa Node >=18 o instala undici.');
    process.exit(1);
  }
}

const TZ = process.env.TZ || 'Europe/Madrid';

const fmtDateISO = d => new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle:'short' }).format(d);  // yyyy-mm-dd
const fmtDateES  = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, day:'2-digit', month:'2-digit', year:'numeric' }).format(d);
const fmtTime    = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, hour:'2-digit', minute:'2-digit', hour12:false }).format(d);
const weekdayES  = d => {
  const s = new Intl.DateTimeFormat('es-ES', { timeZone: TZ, weekday:'long' }).format(d);
  return s.charAt(0).toUpperCase() + s.slice(1);
};

function isMonday(){
  return new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday:'short' })
           .format(new Date()).toLowerCase()==='mon';
}
function isWeekend(){
  const wd = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday:'short' })
               .format(new Date()).toLowerCase();
  return wd==='sat' || wd==='sun';
}
function weekRangeDates(){
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff = wd===0 ? -6 : 1-wd;
  const mon = new Date(d); mon.setDate(d.getDate()+diff);
  const sun = new Date(mon); sun.setDate(mon.getDate()+6);
  return { mon, sun };
}
function weekRangeES(){
  const { mon, sun } = weekRangeDates();
  return { monday: fmtDateES(mon), sunday: fmtDateES(sun) };
}

/* ---------------- HTTP con timeout + reintentos ---------------- */
async function fetchWithTimeout(url, {
  timeoutMs=15000, retries=2, retryDelayBaseMs=800, method='GET', headers={}, body
}={}){
  await ensureHTTP();
  let lastErr;
  for (let i=0;i<=retries;i++){
    const ctrl = new _AbortController();
    const timer = setTimeout(()=>ctrl.abort(new Error('Timeout')), timeoutMs);
    try{
      const res = await _fetch(url, { method, headers, body, signal: ctrl.signal });
      clearTimeout(timer);
      if (!res.ok) {
        let txt=''; try{ txt = await res.text(); }catch{}
        throw new Error(`HTTP ${res.status}${txt?` — ${txt.slice(0,200)}`:''}`);
      }
      return res;
    }catch(e){
      clearTimeout(timer);
      lastErr = e;
      if (i<retries) await new Promise(r=>setTimeout(r, retryDelayBaseMs*(i+1)));
    }
  }
  throw lastErr || new Error('fetch failed');
}

/* ---------------- Fuente: Investing widget (HTML) ----------------
   Endpoint: https://ec.forexprostools.com/ (embed)
   Parámetros usados:
     - country=5              (Estados Unidos)
     - importance=2,3         (medium/high)
     - dateFrom=YYYY-MM-DD
     - dateTo=YYYY-MM-DD
     - timeZone=56            (~Europe/Madrid en el widget)
     - lang=12                (español)
     - columns=exc_date,exc_time,exc_event,exc_importance
------------------------------------------------------------------*/
function buildInvestingURL({dateFrom, dateTo, importance, country='5', timeZone='56', lang='12'}){
  const cols = 'exc_date,exc_time,exc_event,exc_importance';
  const params = new URLSearchParams({
    country, importance, timeZone, lang,
    dateFrom, dateTo, columns: cols
  });
  return `https://ec.forexprostools.com/?${params.toString()}`;
}

/* ---------------- Parseador HTML básico ---------------- */
function parseInvestingHTML(html){
  // El widget devuelve una tabla con <tr> por evento.
  // Vamos a extraer con regex/DOM-lite sin dependencias externas.
  const rows = [];
  // Romper por filas
  const trRegex = /<tr[^>]*?>([\s\S]*?)<\/tr>/gi;
  let m;
  while ((m = trRegex.exec(html)) !== null){
    const tr = m[1];

    // Columna hora (ej: 14:30)
    const timeMatch = tr.match(/<td[^>]*class="first-time"[^>]*>([\s\S]*?)<\/td>/i)
                    || tr.match(/<td[^>]*data-title="Hora"[^>]*>([\s\S]*?)<\/td>/i)
                    || tr.match(/<td[^>]*class="time"[^>]*>([\s\S]*?)<\/td>/i);
    let time = sanitize(stripTags(timeMatch ? timeMatch[1] : ''));

    // Fecha (día) aparece como fila separadora o en columna; capturamos si está en este <tr>
    const dateMatch = tr.match(/<td[^>]*data-title="Fecha"[^>]*>([\s\S]*?)<\/td>/i)
                   || tr.match(/<td[^>]*class="theDay"[^>]*>([\s\S]*?)<\/td>/i);
    let date = sanitize(stripTags(dateMatch ? dateMatch[1] : ''));

    // Evento (texto en español si lang=12)
    const evMatch = tr.match(/<td[^>]*class="event"[^>]*>([\s\S]*?)<\/td>/i)
                  || tr.match(/<td[^>]*data-title="Evento"[^>]*>([\s\S]*?)<\/td>/i);
    let title = sanitize(stripTags(evMatch ? evMatch[1] : ''));

    // Impacto: cuentan los "bullX" / estrellas. En el widget a veces son <i class="icon icon--bullish N"> o imgs.
    // Buscamos 3 o 2 marcas:
    let stars = '⭐️⭐️'; // por defecto medium
    if (/bull(3|ish\s*3)|star.?3|alta/i.test(tr)) stars = '⭐️⭐️⭐️';
    else if (/bull(2|ish\s*2)|star.?2|media/i.test(tr)) stars = '⭐️⭐️';

    // Filtrar filas que no son eventos (cabeceras vacías)
    if (!title || !time) continue;

    rows.push({ date, time, title, stars });
  }
  return rows;
}

function stripTags(s){ return String(s).replace(/<[^>]*>/g,''); }
function sanitize(s){ return stripTags(s).replace(/\s+/g,' ').trim(); }

/* ---------------- Normalización a estructura por día ---------------- */
function groupByDay(rows){
  // Algunas filas no traen fecha en cada <tr>; Investing mete separadores de día en filas aparte.
  // Estrategia: recordamos el "día actual" cuando encontramos una fila que trae fecha,
  // y lo aplicamos a las siguientes filas hasta que cambie.
  const out = [];
  let currentDay = null;

  for (const r of rows){
    // Si la 'date' luce como 'martes, 07 oct' o '07/10/2025', la adoptamos
    if (r.date && r.date.length >= 6) currentDay = r.date;
    if (!currentDay) continue; // no sabemos a qué día pertenece aún

    out.push({
      dayLabel: normalizeDayES(currentDay),   // "Martes 07/10/2025"
      time: r.time,
      title: decorateTitleES(r.title),
      stars: r.stars
    });
  }
  return out;
}

function normalizeDayES(s){
  // Intentar convertir formatos tipo "martes, 07 oct" a DD/MM/YYYY si fuera posible;
  // si no, lo dejamos capitalizado.
  const cap = s.charAt(0).toUpperCase() + s.slice(1);
  // Si ya viene con formato dd/mm o dd/mm/aaaa, lo dejamos.
  if (/\d{1,2}\/\d{1,2}/.test(cap)) return cap;
  return cap;
}

function decorateTitleES(t){
  const x = t.toLowerCase();
  if (/ipc|cpi|inflaci|pce/.test(x)) return '📊 ' + t;
  if (/nfp|no agrícola|payroll|desempleo/.test(x)) return '📊 ' + t;
  if (/fomc|powell|fed/.test(x)) return '🗣️ ' + t;
  return t;
}

/* ---------------- Construcción de mensaje ---------------- */
function buildWeeklyMessage(events){
  const { monday, sunday } = weekRangeES();
  const header = `🗓️ Calendario Económico (🇺🇸) — Semana ${monday}–${sunday} (${TZ})\nImpacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)\n`;
  if (!events.length) return `${header}\nNo hay eventos de EE. UU. con el filtro actual.`;

  // Agrupar por dayLabel manteniendo orden
  const map = new Map();
  for (const e of events){
    if (!map.has(e.dayLabel)) map.set(e.dayLabel, []);
    map.get(e.dayLabel).push(e);
  }

  const lines = [header];
  for (const [day, arr] of map){
    lines.push(day);
    const MAX = 5;
    const slice = arr.slice(0, MAX);
    for (const ev of slice){
      lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
    }
    if (arr.length > MAX) lines.push(`  +${arr.length - MAX} más…`);
    lines.push('');
  }
  return limitTelegram(lines.join('\n').trim());
}

function buildDailyMessage(events){
  const today = fmtDateES(new Date());
  const header = `🗓️ Calendario (🇺🇸) — Hoy ${today} (${TZ})\nImpacto: ⭐️⭐️ / ⭐️⭐️⭐️\n`;
  if (!events.length) return `${header}\nHoy no hay eventos de EE. UU.`;

  // Suponemos que todos pertenecen al mismo día (hoy)
  const lines = [header];
  for (const ev of events){
    lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
  }
  return limitTelegram(lines.join('\n').trim());
}

function limitTelegram(txt){
  return txt.length > 3900 ? (txt.slice(0, 3870) + '\n…recortado') : txt;
}

/* ---------------- Telegram ---------------- */
async function sendTelegramText(token, chatId, text){
  await ensureHTTP();
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const body = new URLSearchParams({
    chat_id: chatId, text, parse_mode:'HTML', disable_web_page_preview:'true'
  });
  const res = await fetchWithTimeout(url, { method:'POST', body, headers:{'Content-Type':'application/x-www-form-urlencoded'} });
  const json = await res.json();
  if (!json.ok) throw new Error(`sendMessage Telegram error: ${JSON.stringify(json)}`);
  console.log('Telegram OK');
}

/* ---------------- Main ---------------- */
(async ()=>{
  if ((process.env.BLOCK_WEEKENDS||'').trim()==='1' && isWeekend()){
    console.log('Fin de semana (Europe/Madrid) → no ejecuto.');
    return;
  }

  const token = process.env.INVESTX_TOKEN, chatId = process.env.CHAT_ID;
  if (!token || !chatId){ console.error('Faltan INVESTX_TOKEN / CHAT_ID'); process.exit(1); }

  const weekly = isMonday(); // Lunes => semanal
  const importance = (process.env.IMPACT_MIN||'medium').toLowerCase()==='high' ? '3' : '2,3';

  let dateFrom, dateTo;
  if (weekly){
    const { mon, sun } = weekRangeDates();
    dateFrom = fmtDateISO(mon);
    dateTo   = fmtDateISO(sun);
  } else {
    const d = new Date();
    dateFrom = fmtDateISO(d);
    dateTo   = fmtDateISO(d);
  }

  const url = buildInvestingURL({ dateFrom, dateTo, importance });
  console.log('URL Investing:', url);

  const res = await fetchWithTimeout(url, {
    headers: { 'User-Agent':'Mozilla/5.0', 'Accept':'text/html' },
    timeoutMs: 20000,
    retries: 2
  });
  const html = await res.text();

  const rawRows = parseInvestingHTML(html);
  console.log(`Filas parseadas (crudas): ${rawRows.length}`);

  // Convertir a estructura por día con títulos decorados + estrellas
  let events = groupByDay(rawRows);

  // Filtro extra: si pediste solo high
  if ((process.env.IMPACT_MIN||'medium').toLowerCase()==='high'){
    events = events.filter(e => e.stars === '⭐️⭐️⭐️');
  }

  console.log(`Eventos tras filtros: ${events.length}`);

  const msg = weekly ? buildWeeklyMessage(events) : buildDailyMessage(events);
  await sendTelegramText(token, chatId, msg);

  console.log('Fin OK');
})().catch(e=>{ console.error('ERROR:', e && e.stack ? e.stack : e); process.exit(1); });
