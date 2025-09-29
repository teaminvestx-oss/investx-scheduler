/* InvestX Economic Calendar — versión SIN Puppeteer (robusta en Render)
   - Descarga el HTML del widget oficial de Investing (server-side)
   - Filtra USA + importancia 2/3
   - Envía resumen a Telegram
   - Intenta generar PNG propio con PureImage (si falla, no rompe)
   Requiere: INVESTX_TOKEN, CHAT_ID
*/

const fs = require('fs');
const path = require('path');
const cheerio = require('cheerio');
const PImage = require('pureimage');

// ---------- Utilidades de tiempo (Europe/Madrid) ----------
function isMonday(tz = 'Europe/Madrid') {
  const wd = new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'short' })
    .format(new Date()).toLowerCase();
  return wd === 'mon';
}
function todayISO(tz = 'Europe/Madrid') {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: tz, dateStyle: 'short' }).format(new Date());
}
function weekRangeISO(tz = 'Europe/Madrid') {
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:tz,weekday:'short'}).format(d).toLowerCase());
  const diffToMon = wd === 0 ? -6 : 1 - wd;
  const monday = new Date(d); monday.setDate(d.getDate() + diffToMon);
  const sunday = new Date(monday); sunday.setDate(monday.getDate() + 6);
  const f = (x) => new Intl.DateTimeFormat('sv-SE',{timeZone:tz,dateStyle:'short'}).format(x);
  return { monday: f(monday), sunday: f(sunday) };
}
function nowInTZ(tz='Europe/Madrid'){
  const d=new Date();
  const fmt=new Intl.DateTimeFormat('sv-SE',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  const parts=Object.fromEntries(fmt.formatToParts(d).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

// ---------- Descarga y parseo del widget ----------
/*
 Endpoint (widget oficial):
 https://ec.forexprostools.com/?columns=exc,cur,event,act,for,pre&importance=2,3&countries=5&calType=day|week&timeZone=56
 - countries=5 → USA
 - importance=2,3 → 2 y 3 estrellas
 - calType=day/week → día actual o semana
 - timeZone=56 ~ Madrid/CET (aprox; el endpoint devuelve horas locales)
*/
async function fetchCalendar(calType) {
  const url = `https://ec.forexprostools.com/?columns=exc,cur,event,act,for,pre&importance=2,3&countries=5&calType=${calType}&timeZone=56`;
  const res = await fetch(url, {
    headers: {
      'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
      'Accept-Language':'es-ES,es;q=0.9'
    }
  });
  if (!res.ok) throw new Error(`Fetch calendar failed: ${res.status}`);
  const html = await res.text();
  return parseCalendar(html);
}

function parseCalendar(html) {
  const $ = cheerio.load(html);
  const rows = $('tr[id^="eventRowId_"], tr.js-event-item, tr[data-event-datetime]');
  const events = [];
  rows.each((_, tr) => {
    const $tr = $(tr);
    // hora (primera columna del widget suele ser la hora)
    const time = ($tr.find('td').first().text() || '').trim();

    // importancia: número de <i> o <span> en la celda de sentimiento
    let imp = 0;
    const $sent = $tr.find('td.sentiment, td.impact, .sentiment');
    if ($sent.length) {
      const n = $sent.find('i, svg, span').length;
      imp = n >= 3 ? 3 : (n >= 2 ? 2 : (n >= 1 ? 1 : 0));
    }

    // título del evento
    const title = (
      $tr.attr('data-event-title') ||
      $tr.find('td.event, td.left, td:nth-child(3)').text() ||
      $tr.text()
    ).replace(/\s+/g,' ').trim();

    if (!title || title.length < 4) return;

    const forecast = ($tr.find('td.fore, td.forecast').text() || '').trim();
    const previous = ($tr.find('td.prev, td.previous').text() || '').trim();
    const iso = $tr.attr('data-event-datetime') || '';

    // Filtrado redundante: importancia 2/3 ya viene del endpoint, pero por si acaso:
    if (imp >= 2 || /⭐/.test($sent.text()||'')) {
      events.push({ time, title, importance: imp || 2, forecast, previous, iso });
    }
  });
  return { events };
}

// ---------- PNG simple con PureImage ----------
async function drawPNG(events, caption) {
  try {
    const width = 1200;
    const rowH = 56;
    const headerH = 100;
    const h = headerH + rowH * Math.min(events.length, 18) + 40;
    const img = PImage.make(width, h);
    const ctx = img.getContext('2d');

    // fondo
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0,0,width,h);

    // Tipografía: intenta cargar DejaVuSans del sistema (si no, PureImage usará fallback)
    const fontPath1 = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf';
    if (fs.existsSync(fontPath1)) {
      const f = PImage.registerFont(fontPath1, 'UI');
      await f.load();
    }
    ctx.fillStyle = '#111111';

    // Título
    ctx.font = '32pt UI, Arial';
    ctx.fillText('Calendario económico USA (⭐️⭐️/⭐️⭐️⭐️)', 28, 56);
    ctx.font = '18pt UI, Arial';
    ctx.fillStyle = '#444444';
    ctx.fillText(caption, 28, 86);

    // Cabeceras
    ctx.fillStyle = '#222222';
    ctx.font = '16pt UI, Arial';
    ctx.fillText('Hora', 28, headerH);
    ctx.fillText('Evento', 150, headerH);
    ctx.fillText('Forecast', 900, headerH);
    ctx.fillText('Previo', 1040, headerH);

    // separador
    ctx.strokeStyle = '#e5e7eb';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(20, headerH+10); ctx.lineTo(width-20, headerH+10); ctx.stroke();

    // Filas
    ctx.font = '15pt UI, Arial';
    let y = headerH + 40;
    const list = events.slice(0, 18);
    for (const e of list) {
      ctx.fillStyle = '#111111';
      ctx.fillText(e.time || '--:--', 28, y);

      // evento (wrap simple)
      const maxW = 720;
      let t = e.title || '';
      if (ctx.measureText(t).width > maxW) {
        while (t.length && ctx.measureText(t+'…').width > maxW) t = t.slice(0, -1);
        t += '…';
      }
      ctx.fillText(t, 150, y);

      ctx.fillStyle = '#2563eb';
      ctx.fillText(e.forecast || '-', 900, y);

      ctx.fillStyle = '#6b7280';
      ctx.fillText(e.previous || '-', 1040, y);

      // rayita
      ctx.strokeStyle = '#f3f4f6';
      ctx.beginPath(); ctx.moveTo(20, y+14); ctx.lineTo(width-20, y+14); ctx.stroke();

      y += rowH;
    }

    const out = fs.createWriteStream('calendar.png');
    await PImage.encodePNGToStream(img, out);
    await new Promise(r => out.on('finish', r));
    return true;
  } catch (e) {
    console.error('PNG generation failed:', e.message);
    return false;
  }
}

// ---------- Resumen ----------
function buildSummary(events) {
  if (!events || !events.length) return '';
  // Top 3 por importancia (3→2→1) conservando orden
  const sorted = [...events].sort((a,b)=> (b.importance||0)-(a.importance||0));
  const top = sorted.slice(0, 3);
  const blocks = top.map(e => {
    const meta = [];
    if (e.forecast) meta.push(`consenso ${e.forecast}`);
    if (e.previous) meta.push(`anterior ${e.previous}`);
    const extra = meta.length ? ` — ${meta.join(', ')}` : '';
    return `📌 <b>${e.title}</b> (${e.time || '--:--'})${extra}`;
  });
  return "📰 <b>Resumen principales noticias</b>\n\n" + blocks.join("\n\n");
}

// ---------- Telegram ----------
async function sendTelegramPhoto(token, chatId, caption, filePath) {
  const url = `https://api.telegram.org/bot${token}/sendPhoto`;
  const form = new FormData();
  form.append('chat_id', chatId);
  form.append('caption', caption);
  form.append('parse_mode', 'HTML');
  form.append('photo', new Blob([fs.readFileSync(filePath)]), path.basename(filePath));
  const res = await fetch(url, { method: 'POST', body: form });
  if (!res.ok) throw new Error(`sendPhoto failed: ${res.status} ${await res.text()}`);
}
async function sendTelegramText(token, chatId, htmlText) {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const body = new URLSearchParams({
    chat_id: chatId, text: htmlText, parse_mode: 'HTML', disable_web_page_preview: 'true'
  });
  const res = await fetch(url, { method: 'POST', body });
  if (!res.ok) throw new Error(`sendMessage failed: ${res.status} ${await res.text()}`);
}

// ---------- Main ----------
(async () => {
  const token = process.env.INVESTX_TOKEN;
  const chatId = process.env.CHAT_ID;
  if (!token || !chatId) { console.error('Faltan INVESTX_TOKEN / CHAT_ID'); process.exit(1); }

  console.log(`[${nowInTZ()}] Descargando calendario…`);
  const mode = isMonday() ? 'week' : 'day';
  const { events = [] } = await fetchCalendar(mode);

  // Caption
  const { monday, sunday } = weekRangeISO();
  const caption = isMonday()
    ? `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Semana ${monday}–${sunday}`
    : `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Hoy ${todayISO()}`;

  // PNG (opcional, no rompe si falla)
  let sentImage = false;
  if (events.length) {
    const ok = await drawPNG(events, caption);
    if (ok && fs.existsSync('calendar.png')) {
      console.log('Enviando imagen…');
      await sendTelegramPhoto(token, chatId, caption, 'calendar.png');
      sentImage = true;
    }
  } else {
    console.log('Sin eventos obtenidos del widget.');
  }

  // Resumen (siempre que haya eventos)
  const summary = buildSummary(events);
  if (summary) {
    console.log('Enviando resumen…');
    await sendTelegramText(token, chatId, summary);
  } else if (!sentImage) {
    // Mensaje mínimo para no “quedarme callado”
    await sendTelegramText(token, chatId, `🗓️ ${caption}\n\n(No hay eventos relevantes hoy).`);
  }

  console.log('Hecho.');
})().catch(err => {
  console.error('ERROR:', err);
  process.exit(1);
});

