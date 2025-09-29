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
  return new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'short' })
    .format(new Date()).toLowerCase() === 'mon';
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
  // móvil: el panel de filtros también existe
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
async function navWithRetry(page, url, tries = 3) {
  let lastErr;
  for (let i = 0; i < tries; i++) {
    try {
      await page.goto(url, { waitUntil: 'networkidle2', timeout: 180000 });
      if (typeof page.waitForNetworkIdle === 'function') {
        try { await page.waitForNetworkIdle({ timeout: 15000 }); } catch {}
      }
      await wait(1200);
      return;
    } catch (e) {
      lastErr = e;
      try { await page.reload({ waitUntil: 'domcontentloaded', timeout: 90000 }); } catch {}
      await wait(1200);
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
    defaultViewport: { width: 414, height: 896, deviceScaleFactor: 2 }, // móvil = más ligero
    protocolTimeout: 300000 // 5 min
  });

  const page = await browser.newPage();
  page.setDefaultTimeout(120000);
  page.setDefaultNavigationTimeout(180000);

  // Bloquea recursos pesados (acelera carga)
  await page.setRequestInterception(true);
  page.on('request', req => {
    const type = req.resourceType();
    if (['image','media','font','stylesheet','websocket'].includes(type)) {
      return req.abort();
    }
    req.continue();
  });

  // UA móvil + idioma español
  await page.setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1');
  await page.setExtraHTTPHeaders({ 'Accept-Language':'es-ES,es;q=0.9' });

  // Versión móvil (más rápida)
  await navWithRetry(page, 'https://m.investing.com/economic-calendar/', 3);

  // cookies
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("button,a,[role='button']")]
      .find(x => /aceptar|accept|consent|agree/i.test((x.innerText || '')));
    b?.click();
  }).catch(()=>{});
  await wait(400);

  // Lunes → "Esta semana", resto → "Hoy"
  if (isMonday()) { await clickByText(page, 'a,button', ['esta semana','this week','semana'], 6000); }
  else            { await clickByText(page, 'a,button', ['hoy','today'], 6000); }
  await wait(400);

  await applyFilters(page);
  await wait(600);

  // espera a tabla/lista (mobile usa listas)
  try {
    await page.waitForSelector('#economicCalendarData, table.genTbl, ul, .ecEvents', { timeout: 90000 });
    if (typeof page.waitForNetworkIdle === 'function') {
      try { await page.waitForNetworkIdle({ timeout: 10000 }); } catch {}
    }
  } catch {}

  // Extraer eventos (tolerante a HTML móvil/escritorio)
  const data = await page.evaluate(() => {
    const pick = (...xs) => xs.find(v => v && v.trim && v.trim().length > 0);
    const rows = [
      ...document.querySelectorAll('tr[id^="eventRowId_"],tr.js-event-item,tr[data-event-datetime]'),
      ...document.querySelectorAll('li[id^="eventRowId_"], li.js-event-item')
    ];

    const events = [];
    for (const el of rows) {
      const isHidden = (el.style && el.style.display === 'none');
      if (isHidden) continue;

      // título robusto
      const byAttr = el.getAttribute('data-event-title');
      const aTitle = el.querySelector('[title]')?.getAttribute('title');
      const aria   = el.querySelector('[aria-label]')?.getAttribute('aria-label');
      const txt1   = el.querySelector('a, .event')?.textContent;
      const txt2   = el.textContent;
      let title = (pick(byAttr,aTitle,aria,txt1,txt2) || '').replace(/\s+/g,' ').trim();
      if (/^\d{1,2}:\d{2}$/.test(title) || title.length < 4) continue;

      // hora
      let time = '';
      const t1 = el.querySelector('td:first-child, .time, [data-event-datetime]');
      if (t1) time = (t1.innerText || t1.textContent || '').trim();

      // importancia
      let imp = parseInt(el.getAttribute('data-importance') || '0', 10);
      if (!imp || isNaN(imp)) {
        const s = el.querySelector('.sentiment, .impact');
        if (s) {
          const n = s.querySelectorAll('i,svg').length;
          imp = n >= 3 ? 3 : (n >= 2 ? 2 : 1);
        } else { imp = 0; }
      }

      const forecast = (el.querySelector('.fore, .forecast')?.textContent || '').trim();
      const previous = (el.querySelector('.prev, .previous')?.textContent || '').trim();
      const iso = el.getAttribute('data-event-datetime') || '';

      events.push({ time, title, importance: imp, iso, forecast, previous });
    }
    return { events };
  });

  // Screenshot básico (si existe algo parecido a tabla/lista)
  const table = await page.$('#economicCalendarData') || await page.$('table.genTbl') || await page.$('.ecEvents, ul');
  if (table) { await table.screenshot({ path: 'calendar.png' }); }

  fs.writeFileSync('calendar.json', JSON.stringify(data, null, 2));
  await browser.close();
  return data;
}

// ---------- Resumen simple (estable) ----------
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
