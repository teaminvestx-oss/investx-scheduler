/* InvestX Economic Calendar — feed JSON (ForexFactory)
   - Fuente: https://nfs.faireconomy.media/ff_calendar_thisweek.json
   - Filtra USD + impacto Medium/High (≈ ⭐⭐/⭐⭐⭐)
   - Lunes -> semana completa; Mar–Vie -> solo hoy (TZ Europe/Madrid)
   - Requiere env vars: INVESTX_TOKEN, CHAT_ID
*/

const fs = require('fs');
const path = require('path');
const PImage = require('pureimage');

const TZ = 'Europe/Madrid';
const fmtDate = (d) => new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle: 'short' }).format(d);
const fmtTime = (d) => new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }).format(d);
const nowStamp = () => {
  const d = new Date();
  const p = Object.fromEntries(new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }).formatToParts(d).map(x=>[x.type,x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
};
const isMonday = () => {
  const wd = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short' }).format(new Date()).toLowerCase();
  return wd === 'mon';
};
const weekRangeISO = () => {
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat'].indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff = wd === 0 ? -6 : 1 - wd;
  const mon = new Date(d); mon.setDate(d.getDate() + diff);
  const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
  return { monday: fmtDate(mon), sunday: fmtDate(sun) };
};

// Fetch JSON feed (ForexFactory)
async function fetchFFWeek() {
  const url = `https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`;
  const res = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0',
      'Accept': 'application/json',
      'Accept-Language': 'es-ES,es;q=0.9'
    }
  });
  if (!res.ok) throw new Error(`Fetch week failed: ${res.status}`);
  return res.json();
}

// Filter events: USD + impact medium/high
function filterEvents(raw, onlyToday) {
  const todayStr = fmtDate(new Date());
  return raw
    .filter(e => (e.country || '').toUpperCase() === 'USD')
    .filter(e => /medium|high/i.test(e.impact || ''))
    .map(e => {
      const ts = e.timestamp ? Number(e.timestamp) * 1000 : Date.now();
      const dt = new Date(ts);
      return {
        date: fmtDate(dt),
        time: fmtTime(dt),
        title: (e.title || '').trim(),
        forecast: (e.forecast || '').toString().trim(),
        previous: (e.previous || '').toString().trim(),
        impact: (e.impact || '').toLowerCase()
      };
    })
    .filter(e => (onlyToday ? e.date === todayStr : true))
    .sort((a,b) => (a.date + a.time).localeCompare(b.date + b.time));
}

// Simple PNG generation (PureImage)
async function drawPNG(events, caption) {
  try {
    const width = 1200;
    const rowH = 56;
    const headerH = 100;
    const shown = Math.min(events.length, 22);
    const h = headerH + rowH * shown + 40;

    const img = PImage.make(width, h);
    const ctx = img.getContext('2d');

    // background
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0,0,width,h);

    // attempt register font if present
    const fpath = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf';
    if (fs.existsSync(fpath)) {
      const f = PImage.registerFont(fpath, 'UI'); await f.load();
    }

    // header
    ctx.fillStyle = '#111111';
    ctx.font = '32pt UI, Arial';
    ctx.fillText('Calendario económico USA (⭐️⭐️/⭐️⭐️⭐️)', 28, 56);

    ctx.font = '18pt UI, Arial';
    ctx.fillStyle = '#444444';
    ctx.fillText(caption, 28, 86);

    // columns
    ctx.fillStyle = '#222222';
    ctx.font = '16pt UI, Arial';
    ctx.fillText('Fecha', 28, headerH);
    ctx.fillText('Hora', 140, headerH);
    ctx.fillText('Evento', 230, headerH);
    ctx.fillText('Forecast', 900, headerH);
    ctx.fillText('Previo', 1040, headerH);

    ctx.strokeStyle = '#e5e7eb';
    ctx.beginPath(); ctx.moveTo(20, headerH+10); ctx.lineTo(width-20, headerH+10); ctx.stroke();

    // rows
    ctx.font = '15pt UI, Arial';
    let y = headerH + 40;
    for (const e of events.slice(0, shown)) {
      ctx.fillStyle = '#111111';
      ctx.fillText(e.date, 28, y);
      ctx.fillText(e.time, 140, y);

      // wrap truncated title
      const maxW = 650;
      let t = e.title || '';
      while (t.length && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
      if ((e.title || '').length !== t.length) t += '…';
      ctx.fillText(t, 230, y);

      ctx.fillStyle = '#2563eb';
      ctx.fillText(e.forecast || '-', 900, y);

      ctx.fillStyle = '#6b7280';
      ctx.fillText(e.previous || '-', 1040, y);

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

// Build short summary (HTML-safe tags <b> allowed)
function buildSummary(events, onlyToday) {
  if (!events.length) return '';
  const top = events.slice(0, 4);
  const lines = top.map(e => {
    const meta = [];
    if (e.forecast) meta.push(`consenso ${e.forecast}`);
    if (e.previous) meta.push(`anterior ${e.previous}`);
    const extra = meta.length ? ` — ${meta.join(', ')}` : '';
    return `📌 <b>${e.title}</b> (${onlyToday ? e.time : `${e.date} ${e.time}`})${extra}`;
  });
  return "📰 <b>Resumen principales noticias</b>\n\n" + lines.join("\n\n");
}

// Telegram helpers
async function sendTelegramPhoto(token, chatId, caption, filePath) {
  const url = `https://api.telegram.org/bot${token}/sendPhoto`;
  const form = new FormData();
  form.append('chat_id', chatId);
  form.append('caption', caption);
  form.append('parse_mode', 'HTML');
  form.append('photo', new Blob([fs.readFileSync(filePath)]), path.basename(filePath));
  const r = await fetch(url, { method:'POST', body: form });
  if (!r.ok) throw new Error(`sendPhoto failed: ${r.status} ${await r.text()}`);
}
async function sendTelegramText(token, chatId, htmlText) {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const body = new URLSearchParams({ chat_id: chatId, text: htmlText, parse_mode: 'HTML', disable_web_page_preview: 'true' });
  const r = await fetch(url, { method:'POST', body });
  if (!r.ok) throw new Error(`sendMessage failed: ${r.status} ${await r.text()}`);
}

// Main
(async () => {
  const token = process.env.INVESTX_TOKEN;
  const chatId = process.env.CHAT_ID;
  if (!token || !chatId) { console.error('Faltan INVESTX_TOKEN / CHAT_ID'); process.exit(1); }

  console.log(`[${nowStamp()}] Descargando calendario…`);
  const weekly = isMonday();
  const raw = await fetchFFWeek();            // feed JSON
  const events = filterEvents(raw, !weekly);  // lunes -> semana, resto -> hoy

  const { monday, sunday } = weekRangeISO();
  const caption = weekly
    ? `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Semana ${monday}–${sunday}`
    : `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Hoy ${fmtDate(new Date())}`;

  let sentImage = false;
  if (events.length) {
    const ok = await drawPNG(events, caption);
    if (ok && fs.existsSync('calendar.png')) {
      console.log('Enviando imagen…');
      await sendTelegramPhoto(token, chatId, caption, 'calendar.png');
      sentImage = true;
    }
  } else {
    console.log('No hay eventos filtrados (USD + Medium/High).');
  }

  const summary = buildSummary(events, !weekly);
  if (summary) {
    console.log('Enviando resumen…');
    await sendTelegramText(token, chatId, summary);
  } else if (!sentImage) {
    await sendTelegramText(token, chatId, `🗓️ ${caption}\n\n(No hay eventos relevantes).`);
  }

  console.log('Hecho.');
})().catch(err => { console.error('ERROR:', err); process.exit(1); });
