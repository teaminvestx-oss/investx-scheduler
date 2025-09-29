/* InvestX Economic Calendar — Render Cron
   Stack: puppeteer-core + @sparticuz/chromium (robusto en Render)
   ENV: INVESTX_TOKEN, CHAT_ID
   Node: 20
*/

const fs = require('fs');
const path = require('path');
const chromium = require('@sparticuz/chromium');
const puppeteer = require('puppeteer-core');

// ---------- Utilidades de tiempo (Europe/Madrid) ----------
function nowInTZ(tz = 'Europe/Madrid') {
  const d = new Date();
  const fmt = new Intl.DateTimeFormat('sv-SE', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit'
  });
  const parts = Object.fromEntries(fmt.formatToParts(d).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
function isMonday(tz = 'Europe/Madrid') {
  const wd = new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'short' }).format(new Date()).toLowerCase();
  return wd === 'mon';
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

// ---------- Helpers ----------
const wait = (ms)=>new Promise(r=>setTimeout(r,ms));

async function clickByText(page, selector, texts, timeoutMs = 8000) {
  const end = Date.now() + timeoutMs;
  const wants = texts.map(t => t.toLowerCase());
  while (Date.now() < end) {
    const ok = await page.evaluate(({ selector, wants }) => {
      for (const el of document.querySelectorAll(selector)) {
        const t = ((el.innerText || el.textContent || el.value || '') + '').toLowerCase().trim();
        if (wants.some(w => t.includes(w))) { el.click(); return true; }
      }
      return false;
    }, { selector, wants });
    if (ok) return true;
    await wait(250);
  }
  return false;
}

async function applyFilters(page) {
  await clickByText(page, 'button,a,[role="button"],input[type="button"]', ['filtro','filtros','filters'], 8000).catch(()=>{});
  await wait(600);
  await page.evaluate(() => {
    const root = document.querySelector('.filterPopup,[class*="filterPop"]') || document;

    const countries = [...root.querySelectorAll('input[type="checkbox"][name*="country"]')];
    countries.forEach(cb => { cb.checked = false; cb.dispatchEvent(new Event('change', { bubbles:true })); });
    let us = countries.find(cb => cb.value === '5') ||
             countries.find(cb => /estados unidos|united states|ee\.uu/i.test((cb.closest('label,li,div')?.innerText||'')));
    if (us) { us.checked = true; us.dispatchEvent(new Event('change', { bubbles:true })); }

    const imps = [...root.querySelectorAll('input[type="checkbox"][name*="importance"]')];
    imps.forEach(cb => { cb.checked = (cb.value === '2' || cb.value === '3'); cb.dispatchEvent(new Event('change', { bubbles:true })); });

    const btn = [...root.querySelectorAll('button,a,input[type="submit"],input[type="button"]')]
      .find(b => /aplicar|apply|mostrar|show/i.test((b.innerText || b.value || '')));
    btn?.click();
  });
  await wait(1200);
}

// Navegación robusta con reintentos + espera a red ociosa
async function navWithRetry(page, url, tries = 2) {
  let lastErr;
  for (let i = 0; i < tries; i++) {
    try {
      await page.goto(url, { waitUntil: 'networkidle2', timeout: 180000 });
      if (typeof page.waitForNetworkIdle === 'function') {
        try { await page.waitForNetworkIdle(); } catch {}
      }
      await wait(1500);
      return;
    } catch (e) {
      lastErr = e;
      try { await page.reload({ waitUntil: 'domcontentloaded', timeout: 90000 }); } catch {}
      await wait(1500);
    }
  }
  throw lastErr;
}

// ---------- Scraper principal ----------
async function buildCalendar() {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: await chromium.executablePath(),
    args: [
      ...chromium.args,
      '--lang=es-ES,es',
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',  // evita cuelgues por /dev/shm pequeño
      '--single-process'          // útil en instancias pequeñas
    ],
    defaultViewport: { width: 1440, height: 2400, deviceScaleFactor: 2 },
    protocolTimeout: 240000      // 4 min
  });

  const page = await browser.newPage();
  page.setDefaultTimeout(90000);
  page.setDefaultNavigationTimeout(120000);
  await page.setUserAgent('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36');
  await page.setExtraHTTPHeaders({ 'Accept-Language':'es-ES,es;q=0.9' });

  await navWithRetry(page, 'https://es.investing.com/economic-calendar/', 2);

  // cookies
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("button,a,[role='button']")]
      .find(x => /aceptar|accept|consent|agree/i.test((x.innerText || '')));
    b?.click();
  }).catch(()=>{});
  await wait(600);

  // Lunes → "Esta semana", resto → "Hoy"
  if (isMonday()) { await clickByText(page, 'a,button', ['esta semana','this week'], 6000); }
  else            { await clickByText(page, 'a,button', ['hoy','today'], 6000); }
  await wait(500);

  await applyFilters(page);
  await wait(600);

  // espera a tabla + red ociosa (sin romper si no aparece)
  try {
    await page.waitForSelector('#economicCalendarData, table.genTbl', { timeout: 90000 });
    if (typeof page.waitForNetworkIdle === 'function') {
      try { await page.waitForNetworkIdle(); } catch {}
    }
  } catch {}

  // Extraer eventos
  const data = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('tr[id^="eventRowId_"],tr.js-event-item,tr[data-event-datetime]')];

    const cleanTitle = (tr) => {
      const pick = (...xs) => xs.find(v => v && v.trim && v.trim().length > 0);
      const byAttr = tr.getAttribute('data-event-title');
      const aTitle = tr.querySelector('td[class*="event"] a[title]')?.getAttribute('title');
      const aria   = tr.querySelector('td[class*="event"] [aria-label]')?.getAttribute('aria-label');
      const txt1   = tr.querySelector('td[class*="event"] a')?.textContent;
      const txt2   = tr.querySelector('td[class*="event"], td.left, td:nth-child(3)')?.textContent;
      let raw = (pick(byAttr, aTitle, aria, txt1, txt2) || '').replace(/\s+/g,' ').trim();
      if (/^\d{1,2}:\d{2}$/.test(raw) || raw.length < 4) raw = '';
      return raw;
    };

    const events = [];
    for (const tr of rows) {
      if (tr.style.display === 'none') continue; // respeta filtros de UI
      const tds = tr.querySelectorAll('td');
      const time  = (tds[0]?.innerText || '').trim();
      const title = cleanTitle(tr);
      if (!title) continue;

      let imp = parseInt(tr.getAttribute('data-importance') || '0', 10);
      if (!imp || isNaN(imp)) {
        const s = tr.querySelector('td.sentiment,td.impact,.sentiment');
        if (s) {
          const n = s.querySelectorAll('i,svg').length;
          imp = n >= 3 ? 3 : (n >= 2 ? 2 : 1);
        } else { imp = 0; }
      }

      events.push({
        time, title, importance: imp,
        iso: tr.getAttribute('data-event-datetime') || '',
        forecast: (tr.querySelector('td.fore,td.forecast')?.innerText || '').trim(),
        previous: (tr.querySelector('td.prev,td.previous')?.innerText || '').trim()
      });
    }
    return { events };
  });

  // Screenshot si hay tabla visible
  const table = await page.$('#economicCalendarData') || await page.$('table.genTbl');
  if (table) { await table.screenshot({ path: 'calendar.png' }); }

  fs.writeFileSync('calendar.json', JSON.stringify(data, null, 2));
  await browser.close();
  return data;
}

// ---------- Resumen simple (estable) ----------
function normalize(s) {
  return (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/\s+/g,' ').trim().toLowerCase();
}
function buildSummary(events) {
  if (!events || !events.length) return '';
  // Top 3 por importancia (3→2→1) manteniendo orden
  const sorted = [...events].sort((a,b)=> (b.importance||0)-(a.importance||0));
  const pick = sorted.slice(0,3).map(e => `📌 <b>${e.title}</b> (${e.time || '--:--'})`);
  return pick.length ? "📰 <b>Resumen principales noticias</b>\n\n" + pick.join("\n\n") : '';
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
  if (!res.ok) { throw new Error(`sendPhoto failed: ${res.status} ${await res.text()}`); }
}
async function sendTelegramText(token, chatId, htmlText) {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const params = new URLSearchParams();
  params.set('chat_id', chatId);
  params.set('text', htmlText);
  params.set('parse_mode', 'HTML');
  params.set('disable_web_page_preview', 'true');
  const res = await fetch(url, { method: 'POST', body: params });
  if (!res.ok) { throw new Error(`sendMessage failed: ${res.status} ${await res.text()}`); }
}

// ---------- Main ----------
(async () => {
  const token = process.env.INVESTX_TOKEN;
  const chatId = process.env.CHAT_ID;
  if (!token || !chatId) {
    console.error('Faltan variables de entorno INVESTX_TOKEN y/o CHAT_ID');
    process.exit(1);
  }

  console.log(`[${nowInTZ()}] Iniciando scrape…`);
  const { events = [] } = await buildCalendar();

  const pngExists = fs.existsSync('calendar.png');
  const todayISO = new Intl.DateTimeFormat('sv-SE', { timeZone:'Europe/Madrid', dateStyle: 'short' }).format(new Date());
  const { monday, sunday } = weekRangeISO('Europe/Madrid');
  const caption = isMonday()
    ? `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Semana ${monday}–${sunday}`
    : `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Hoy ${todayISO}`;

  if (pngExists) {
    console.log('Enviando imagen a Telegram…');
    await sendTelegramPhoto(token, chatId, caption, 'calendar.png');
  } else {
    console.log('No se generó calendar.png (seguimos con el resumen si aplica)');
  }

  const summary = buildSummary(events);
  if (summary) {
    console.log('Enviando resumen a Telegram…');
    await sendTelegramText(token, chatId, summary);
  } else {
    console.log('Sin resumen relevante.');
  }

  console.log('Hecho.');
})().catch(err => {
  console.error('ERROR:', err);
  process.exit(1);
});
