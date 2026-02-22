function $(id){ return document.getElementById(id); }

function fmtMoney(x){
  if (x === null || x === undefined) return "—";
  return Number(x).toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function fmtPrice(x){
  if (x === null || x === undefined) return "—";
  return Number(x).toLocaleString(undefined, { maximumFractionDigits: 4 });
}
function fmtPct(x){
  if (x === null || x === undefined) return "—";
  return `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)}%`;
}
function hasEl(id){ return !!$(id); }

let COMPANIES = [];
let SECTORS = [];
let lastPrices = {};
let qtyDraft = {};
let currentNews = null;
let adminAuthed = false;
let quoteCache = {};
let historyCache = {};
let ohlcCache = {};
let chartTicker = null;
let chartMode = "line";
let impactMapCache = {};

function fmtTime(ts){
  if(!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour:"2-digit", minute:"2-digit", second:"2-digit" });
}

function showToast(msg){
  const el = $("toast");
  if(!el) return;
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(window.__toastT);
  window.__toastT = setTimeout(()=>{ el.style.display = "none"; }, 1800);
}

function secondsToMMSS(s){
  if (s === null || s === undefined) return "—";
  s = Math.max(0, Math.floor(s));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2,"0")}`;
}

/* =========================
   Home page join fix (important)
   ========================= */
function setupJoinPage(){
  // Try known IDs first
  const form = $("joinForm") || document.querySelector('form[action="/game"]');
  const input = $("playerInput") || document.querySelector('input[name="player"]');
  const btn = $("joinBtn");
  const fallback = $("joinBtnFallback");

  if(!form && !input && !btn && !fallback) return; // not a join page UI

  const goToGame = () => {
    const name = (input?.value || "").trim();
    if(!name){
      input?.focus();
      return;
    }
    window.location.href = `/game?player=${encodeURIComponent(name)}`;
  };

  if(form){
    form.addEventListener("submit", (e) => {
      const name = (input?.value || "").trim();
      if(!name){
        e.preventDefault();
        input?.focus();
        return;
      }
      if(input) input.value = name;
      // let normal GET submit happen
    });
  }

  if(btn){
    // if custom UI button is accidentally type=button, force navigation
    btn.addEventListener("click", (e) => {
      const type = (btn.getAttribute("type") || "").toLowerCase();
      if(type === "button"){
        e.preventDefault();
        goToGame();
      }
    });
  }

  if(fallback){
    fallback.addEventListener("click", (e) => {
      e.preventDefault();
      goToGame();
    });
  }

  if(input){
    input.addEventListener("keydown", (e) => {
      if(e.key === "Enter"){
        // if form exists, let submit happen; otherwise force navigation
        if(!form){
          e.preventDefault();
          goToGame();
        }
      }
    });
  }
}

/* =========================
   Filters / Market table
   ========================= */
function applyFilters(){
  const q = ($("search")?.value || "").trim().toLowerCase();
  const sec = ($("sectorFilter")?.value || "");
  const tbody = $("marketBody");
  if(!tbody) return;

  for(const tr of tbody.querySelectorAll("tr")){
    const ticker = tr.getAttribute("data-ticker") || "";
    const name = tr.getAttribute("data-name") || "";
    const sector = tr.getAttribute("data-sector") || "";
    const okQ = !q || ticker.toLowerCase().includes(q) || name.toLowerCase().includes(q);
    const okS = !sec || sector === sec;
    tr.style.display = (okQ && okS) ? "" : "none";
  }
}

function fillSectorFilter(){
  const sel = $("sectorFilter");
  if(!sel) return;
  sel.innerHTML = `<option value="">All sectors</option>`;
  for(const s of SECTORS){
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    sel.appendChild(opt);
  }
}

function buildMarketTable(){
  const tbody = $("marketBody");
  if(!tbody) return;
  tbody.innerHTML = "";

  for(const c of COMPANIES){
    const tr = document.createElement("tr");
    tr.setAttribute("data-ticker", c.ticker);
    tr.setAttribute("data-name", c.name);
    tr.setAttribute("data-sector", c.sector);

    tr.innerHTML = `
      <td class="mono"><b>${c.ticker}</b></td>
      <td>${c.name}</td>
      <td><span class="badge">${c.sector}</span></td>
      <td id="im-${c.ticker}"><span class="impactBadge impact-none">None</span></td>
      <td class="right mono" id="px-${c.ticker}">—</td>
      <td class="right mono" id="mv-${c.ticker}">—</td>
      <td class="right mono smallNum" id="sp-${c.ticker}">—</td>
      <td class="mono spark" id="sk-${c.ticker}">▁▁▁▁▁</td>
      <td class="right">
        <input class="qtyInput" id="qty-${c.ticker}" type="number" min="1" step="1" value="1">
      </td>
      <td class="right">
        <div class="actions">
          <button class="btn small" id="buy-${c.ticker}" type="button">Buy</button>
          <button class="btn small ghost" id="sell-${c.ticker}" type="button">Sell</button>
          <button class="btn small ghost" id="chart-${c.ticker}" type="button">See chart</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);

    const qtyEl = $(`qty-${c.ticker}`);
    qtyEl?.addEventListener("input", ()=>{
      const v = parseInt(qtyEl.value || "1", 10);
      qtyDraft[c.ticker] = (isFinite(v) && v > 0) ? v : 1;
    });

    $(`buy-${c.ticker}`)?.addEventListener("click", ()=> doTrade(c.ticker, "BUY"));
    $(`sell-${c.ticker}`)?.addEventListener("click", ()=> doTrade(c.ticker, "SELL"));
    $(`chart-${c.ticker}`)?.addEventListener("click", ()=> openChartModal(c.ticker));
  }

  $("search")?.addEventListener("input", applyFilters);
  $("sectorFilter")?.addEventListener("change", applyFilters);
  applyFilters();
}

async function bootstrap(){
  // Only needed on pages that actually show market/company data
  if(!hasEl("marketBody") && !hasEl("sectorFilter") && !window.NMG_ADMIN && !window.NMG_PRESENTER){
    return;
  }

  try{
    const r = await fetch("/api/bootstrap");
    if(!r.ok) throw new Error(`Bootstrap failed: ${r.status}`);
    const data = await r.json();

    COMPANIES = data.companies || [];
    SECTORS = data.sectors || [];

    for(const c of COMPANIES){
      lastPrices[c.ticker] = c.start_price;
    }

    fillSectorFilter();
    buildMarketTable();

    if(COMPANIES.length){
      chartTicker = COMPANIES[0].ticker;
      if(hasEl("inlineChartCanvas") || hasEl("chartCanvas")){
        drawChart(chartTicker, chartMode);
      }
    }
  }catch(err){
    console.error(err);
    showToast("Failed to load market data.");
  }
}

/* =========================
   News modal
   ========================= */
function openNewsModal(){
  if(!currentNews) return;
  $("modalTitle") && ($("modalTitle").textContent = currentNews.headline || "");
  $("modalBody") && ($("modalBody").textContent = currentNews.body || "");

  const ul = $("modalBullets");
  if(ul){
    ul.innerHTML = "";
    (currentNews.bullets || []).forEach(b=>{
      const li = document.createElement("li");
      li.textContent = b;
      ul.appendChild(li);
    });
  }

  if($("newsModal")){
    $("newsModal").style.display = "flex";
    $("newsModal").setAttribute("aria-hidden","false");
  }
}
function closeNewsModal(){
  if(!$("newsModal")) return;
  $("newsModal").style.display = "none";
  $("newsModal").setAttribute("aria-hidden","true");
}

function setNews(n){
  currentNews = n;
  const h = $("newsHeadline");
  const s = $("newsSummary");
  const btn = $("readMoreBtn");
  if(!h || !s || !btn) return;

  if(n){
    h.textContent = n.headline || "News update";
    s.textContent = n.summary || "";
    btn.style.display = "inline-flex";
  } else {
    h.textContent = "Waiting for the next headline…";
    s.textContent = "";
    btn.style.display = "none";
  }
}

/* =========================
   Trade
   ========================= */
async function doTrade(ticker, side){
  const player = window.NMG_PLAYER;
  if(!player){
    showToast("Missing player.");
    return;
  }

  const q = qtyDraft[ticker] || parseInt($(`qty-${ticker}`)?.value || "1", 10) || 1;

  try{
    const r = await fetch("/api/trade", {
      method:"POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify({ player, ticker, side, qty: q })
    });

    const data = await r.json().catch(()=>({ ok:false, error:"Trade failed" }));
    if(!r.ok || !data.ok){
      showToast(data.error || "Trade failed");
      return;
    }

    const fillTxt = (data.fill_price !== undefined) ? data.fill_price : "—";
    const spreadTxt = (data.spread_pct !== undefined) ? data.spread_pct : "—";
    const slipTxt = (data.slip_pct !== undefined) ? data.slip_pct : "—";
    showToast(`${side} ${ticker} × ${q} @ ${fillTxt} (spread ${spreadTxt}%, slip ${slipTxt}%)`);
  }catch(err){
    console.error(err);
    showToast("Network error while trading.");
  }
}

/* =========================
   Portfolio / leaderboard / presenter
   ========================= */
function renderHoldings(holdings, prices){
  const body = $("holdingsBody");
  if(!body) return;

  const rows = Object.keys(holdings || {});
  if(rows.length === 0){
    body.innerHTML = `<tr><td colspan="7" class="muted">No holdings yet.</td></tr>`;
    return;
  }

  const tickerToCompany = {};
  for(const c of COMPANIES) tickerToCompany[c.ticker] = c.name;

  body.innerHTML = "";
  for(const t of rows.sort()){
    const h = holdings[t];
    const now = prices[t] || 0;
    const value = now * h.qty;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono"><b>${t}</b></td>
      <td>${tickerToCompany[t] || ""}</td>
      <td class="right mono">${h.qty}</td>
      <td class="right mono">${fmtPrice(h.avg)}</td>
      <td class="right mono">${fmtPrice(now)}</td>
      <td class="right mono ${now >= h.avg ? "moveUp" : "moveDown"}">${fmtMoney((now - h.avg) * h.qty)}</td>
      <td class="right mono"><b>${fmtMoney(value)}</b></td>
    `;
    body.appendChild(tr);
  }
}

function renderLeaderboard(lb){
  const body = $("leaderBody");
  if(!body) return;
  body.innerHTML = "";
  (lb || []).slice(0, 10).forEach((x, idx)=>{
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${idx + 1}</td>
      <td>${x.player}</td>
      <td class="right mono"><b>${fmtMoney(x.total)}</b></td>
    `;
    body.appendChild(tr);
  });
}

function renderReaction(meta){
  const pulseEl = $("pulseText");
  const affectedEl = $("affectedText");
  const progressEl = $("reactionProgress");
  const panelEl = $("reactionPanel");
  if(!pulseEl || !affectedEl || !progressEl || !panelEl) return;

  const pulse = (meta && meta.pulse) ? meta.pulse : "CALM";
  pulseEl.textContent = pulse;
  pulseEl.className = `pulse ${pulse.toLowerCase()}`;

  affectedEl.textContent = (meta && Number.isFinite(meta.affected)) ? String(meta.affected) : "0";

  const progress = (meta && Number.isFinite(meta.progress)) ? meta.progress : 0;
  progressEl.style.width = `${Math.max(0, Math.min(100, progress))}%`;

  panelEl.classList.toggle("active", !!(meta && meta.active));
}

function renderTrades(trades){
  const body = $("tradesBody");
  if(!body) return;
  const rows = trades || [];

  if(rows.length === 0){
    body.innerHTML = `<tr><td colspan="6" class="muted">No trades yet.</td></tr>`;
    return;
  }

  body.innerHTML = "";
  for(const t of [...rows].reverse()){
    const cls = (t.side || "").toUpperCase() === "BUY" ? "moveUp" : "moveDown";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${fmtTime(t.ts)}</td>
      <td class="mono"><b>${t.ticker || ""}</b></td>
      <td class="mono ${cls}">${t.side || ""}</td>
      <td class="right mono">${t.qty || 0}</td>
      <td class="right mono">${fmtPrice(t.price)}</td>
      <td class="right mono">${fmtMoney(t.fee || 0)}</td>
    `;
    body.appendChild(tr);
  }
}

function sparkline(arr){
  if(!arr || !arr.length) return "▁▁▁▁▁";
  const blocks = "▁▂▃▄▅▆▇█";
  const min = Math.min(...arr);
  const max = Math.max(...arr);
  const span = Math.max(1e-9, max - min);
  return arr.slice(-14).map(v => blocks[Math.max(0, Math.min(7, Math.floor(((v - min) / span) * 7)))]).join("");
}

function renderMovers(movers){
  const body = $("moversBody");
  if(!body) return;
  body.innerHTML = "";
  for(const m of (movers || [])){
    const cls = m.pct >= 0 ? "moveUp" : "moveDown";
    const sign = m.pct >= 0 ? "+" : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono"><b>${m.ticker}</b></td>
      <td>${m.name}</td>
      <td><span class="badge">${m.sector}</span></td>
      <td class="right mono">${fmtPrice(m.price)}</td>
      <td class="right mono ${cls}">${sign}${(m.pct * 100).toFixed(2)}%</td>
    `;
    body.appendChild(tr);
  }
}

/* =========================
   Chart modal
   ========================= */
function openChartModal(ticker){
  chartTicker = ticker;
  chartMode = chartMode || "line";
  const modal = ensureChartModal();
  if(!modal){
    showToast("Chart view is unavailable right now.");
    return;
  }

  const title = $("chartTitle");
  const c = COMPANIES.find(x => x.ticker === ticker);
  if(title) title.textContent = `${ticker} · ${c?.name || ""}`;

  modal.style.display = "flex";
  modal.setAttribute("aria-hidden","false");
  drawChart(ticker, chartMode);

  const inlineCard = $("inlineChartCard");
  if(inlineCard) inlineCard.scrollIntoView({ behavior:"smooth", block:"start" });
}

function ensureChartModal(){
  let modal = $("chartModal");
  if(modal) return modal;

  // Only create if chart feature exists on page
  if(!hasEl("marketBody") && !hasEl("inlineChartCanvas")) return null;

  modal = document.createElement("div");
  modal.className = "modal";
  modal.id = "chartModal";
  modal.style.display = "none";
  modal.setAttribute("aria-hidden", "true");
  modal.innerHTML = `
    <div class="modalCard chartCard" role="dialog" aria-modal="true" aria-labelledby="chartTitle">
      <div class="modalTop">
        <div class="modalTitle" id="chartTitle">Stock chart</div>
        <div class="row">
          <button class="btn ghost small" id="lineChartBtn" type="button">Line</button>
          <button class="btn ghost small" id="candleChartBtn" type="button">Candlestick</button>
          <button class="btn ghost small" id="closeChartBtn" type="button">Close</button>
        </div>
      </div>
      <canvas id="chartCanvas" width="820" height="360" aria-label="Stock chart canvas"></canvas>
    </div>
  `;
  document.body.appendChild(modal);

  $("lineChartBtn")?.addEventListener("click", ()=>{
    chartMode = "line";
    if(chartTicker) drawChart(chartTicker, chartMode);
  });
  $("candleChartBtn")?.addEventListener("click", ()=>{
    chartMode = "candle";
    if(chartTicker) drawChart(chartTicker, chartMode);
  });
  $("closeChartBtn")?.addEventListener("click", closeChartModal);
  modal.addEventListener("click", (e)=>{
    if(e.target && e.target.id === "chartModal") closeChartModal();
  });

  return modal;
}

function closeChartModal(){
  const modal = $("chartModal");
  if(!modal) return;
  modal.style.display = "none";
  modal.setAttribute("aria-hidden","true");
}

function drawChartOnCanvas(canvas, ticker, mode){
  if(!canvas) return;
  const ctx = canvas.getContext("2d");
  if(!ctx) return;

  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0c1731";
  ctx.fillRect(0, 0, w, h);

  const pad = 32;
  const dataLine = (historyCache[ticker] || []).slice(-60);
  const dataCandle = (ohlcCache[ticker] || []).slice(-40);

  if(mode === "candle" && dataCandle.length){
    const highs = dataCandle.map(x=>x.h);
    const lows = dataCandle.map(x=>x.l);
    const max = Math.max(...highs), min = Math.min(...lows);
    const span = Math.max(1e-9, max - min);
    const step = (w - pad * 2) / dataCandle.length;

    dataCandle.forEach((bar, i)=>{
      const x = pad + (i + 0.5) * step;
      const yH = h - pad - ((bar.h - min) / span) * (h - pad * 2);
      const yL = h - pad - ((bar.l - min) / span) * (h - pad * 2);
      const yO = h - pad - ((bar.o - min) / span) * (h - pad * 2);
      const yC = h - pad - ((bar.c - min) / span) * (h - pad * 2);
      const up = bar.c >= bar.o;

      ctx.strokeStyle = up ? "#33d17f" : "#ff6a88";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(x, yH);
      ctx.lineTo(x, yL);
      ctx.stroke();

      ctx.fillStyle = up ? "#33d17f" : "#ff6a88";
      const top = Math.min(yO, yC);
      const bh = Math.max(2, Math.abs(yC - yO));
      ctx.fillRect(x - step * 0.28, top, step * 0.56, bh);
    });
  } else if(dataLine.length){
    const max = Math.max(...dataLine), min = Math.min(...dataLine);
    const span = Math.max(1e-9, max - min);

    ctx.strokeStyle = "#4f8cff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    dataLine.forEach((v, i)=>{
      const x = pad + (i / Math.max(1, dataLine.length - 1)) * (w - pad * 2);
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      if(i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(163,190,255,.35)";
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, h - pad);
  ctx.lineTo(w - pad, h - pad);
  ctx.stroke();

  ctx.fillStyle = "#aebddd";
  ctx.font = "12px sans-serif";
  ctx.fillText(mode === "candle" ? "Candlestick" : "Line", pad + 8, pad + 14);
}

function drawChart(ticker, mode){
  drawChartOnCanvas($("chartCanvas"), ticker, mode);
  drawChartOnCanvas($("inlineChartCanvas"), ticker, mode);

  const c = COMPANIES.find(x => x.ticker === ticker);
  const inlineTitle = $("inlineChartTitle");
  if(inlineTitle) inlineTitle.textContent = `${ticker} · ${c?.name || ""} · Chart Window`;
}

/* =========================
   Impact badges / market cells
   ========================= */
function impactBadge(level){
  const v = (level || "NONE").toUpperCase();
  if(v === "DIRECT") return '<span class="impactBadge impact-direct">Direct</span>';
  if(v === "SECTOR") return '<span class="impactBadge impact-sector">Sector</span>';
  if(v === "LINKED") return '<span class="impactBadge impact-linked">Linked</span>';
  return '<span class="impactBadge impact-none">None</span>';
}

function updateMarketCells(prices, quotes = {}, history = {}, impactMap = {}){
  for(const t in prices){
    const px = prices[t];
    const last = lastPrices[t] ?? px;
    lastPrices[t] = px;

    const pct = last === 0 ? 0 : (px - last) / last;
    const cls = pct >= 0 ? "moveUp" : "moveDown";
    const sign = pct >= 0 ? "+" : "";

    const pxEl = $(`px-${t}`);
    const mvEl = $(`mv-${t}`);
    const spEl = $(`sp-${t}`);
    const skEl = $(`sk-${t}`);
    const imEl = $(`im-${t}`);
    const rowEl = pxEl ? pxEl.closest("tr") : null;

    if(pxEl) pxEl.textContent = fmtPrice(px);

    if(mvEl){
      mvEl.textContent = `${sign}${(pct * 100).toFixed(2)}%`;
      mvEl.className = `right mono ${cls}`;
      mvEl.classList.remove("flashUp", "flashDown");
      mvEl.classList.add(pct >= 0 ? "flashUp" : "flashDown");
      setTimeout(()=> mvEl.classList.remove("flashUp", "flashDown"), 350);
    }

    if(spEl){
      const sp = quotes[t]?.spread_pct;
      spEl.textContent = sp ? `${(sp * 100).toFixed(2)}%` : "—";
    }

    if(skEl){
      const series = history[t] || [];
      skEl.textContent = sparkline(series);
      skEl.className = `mono spark ${pct >= 0 ? "up" : "down"}`;
    }

    if(imEl){
      const impact = impactMap[t] || "NONE";
      imEl.innerHTML = impactBadge(impact);

      if(rowEl){
        rowEl.classList.toggle("impact-row", impact !== "NONE");
        rowEl.classList.toggle("nonimpact-row", impact === "NONE");
      }
    }

    const qEl = $(`qty-${t}`);
    if(qEl){
      const d = qtyDraft[t];
      if(d && String(qEl.value) !== String(d)){
        qEl.value = d;
      }
    }
  }
}

/* =========================
   Pollers
   ========================= */
async function pollState(){
  const player = window.NMG_PLAYER;
  const presenter = window.NMG_PRESENTER;

  let url = "/api/state";
  if(player) url += `?player=${encodeURIComponent(player)}`;

  try{
    const r = await fetch(url);
    if(!r.ok) throw new Error(`State poll failed: ${r.status}`);
    const s = await r.json();

    $("roundNo") && ($("roundNo").textContent = s.round);
    $("statusText") && ($("statusText").textContent = s.status);
    $("timerText") && ($("timerText").textContent = secondsToMMSS(s.timer_s));

    setNews(s.news || null);
    renderReaction(s.reaction_meta || null);

    impactMapCache = s.impact_map || {};
    quoteCache = s.quotes || {};
    historyCache = s.history || {};
    ohlcCache = s.ohlc || {};

    updateMarketCells(s.prices || {}, quoteCache, historyCache, impactMapCache);

    if(chartTicker){
      drawChart(chartTicker, chartMode);
    }

    if(player && s.portfolio){
      $("cashText") && ($("cashText").textContent = fmtMoney(s.portfolio.cash));
      $("holdingsText") && ($("holdingsText").textContent = fmtMoney(s.portfolio.holdings_value));
      $("totalText") && ($("totalText").textContent = fmtMoney(s.portfolio.total_value));
      renderHoldings(s.portfolio.holdings || {}, s.prices || {});
      renderTrades(s.portfolio.recent_trades || []);
    }

    renderLeaderboard(s.leaderboard || []);

    if(presenter){
      renderMovers(s.movers || []);
    }
  }catch(err){
    console.error(err);
  }
}

async function adminPoll(){
  try{
    const r = await fetch("/api/admin/state");
    if(!r.ok) throw new Error(`Admin state failed: ${r.status}`);
    const s = await r.json();

    $("roundNo") && ($("roundNo").textContent = s.round);
    $("statusText") && ($("statusText").textContent = s.status);
    $("timerText") && ($("timerText").textContent = secondsToMMSS(s.timer_s));
    $("headlineText") && ($("headlineText").textContent = s.headline || "—");
  }catch(err){
    console.error(err);
  }
}

/* =========================
   Admin
   ========================= */
function scopeLabel(n){
  if((n.tickers || []).length) return "Company";
  const secs = (n.sectors || []);
  return secs.length > 1 ? "Multi" : "Sector";
}

async function adminLoadNews(){
  try{
    const r = await fetch("/api/admin/news");
    if(!r.ok) throw new Error(`Admin news failed: ${r.status}`);
    const data = await r.json();
    window.__NEWS = data.news || [];
    renderNewsTable();
  }catch(err){
    console.error(err);
    showToast("Failed to load news.");
  }
}

function renderNewsTable(){
  const body = $("newsBody");
  if(!body) return;

  const q = ($("newsSearch")?.value || "").trim().toLowerCase();
  body.innerHTML = "";

  for(const n of (window.__NEWS || [])){
    if(q && !(n.headline || "").toLowerCase().includes(q)) continue;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${n.id}</td>
      <td>${n.headline}</td>
      <td><span class="badge">${scopeLabel(n)}</span></td>
      <td class="mono">${n.direction}</td>
      <td class="mono">${n.intensity}</td>
      <td class="right"><button class="btn small" type="button" data-id="${n.id}">Trigger</button></td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll("button[data-id]").forEach(btn=>{
    btn.addEventListener("click", async ()=>{
      if(!adminAuthed) return showToast("Unlock admin first");

      const id = btn.getAttribute("data-id");
      const pass = $("adminPass")?.value || "";

      try{
        const rr = await fetch("/api/admin/trigger", {
          method:"POST",
          headers: { "Content-Type":"application/json" },
          body: JSON.stringify({ password: pass, news_id: id })
        });
        const out = await rr.json().catch(()=>({ ok:false, error:"Failed" }));
        showToast(out.ok ? "News triggered" : (out.error || "Failed"));
      }catch(err){
        console.error(err);
        showToast("Network error.");
      }
    });
  });
}

async function adminLogin(){
  const pass = $("adminPass")?.value || "";
  try{
    const r = await fetch("/api/admin/login", {
      method:"POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify({ password: pass })
    });
    const out = await r.json().catch(()=>({ ok:false }));
    if(out.ok){
      adminAuthed = true;
      if($("randomNewsBtn")) $("randomNewsBtn").disabled = false;
      if($("resetBtn")) $("resetBtn").disabled = false;
      showToast("Admin unlocked");
    } else {
      showToast("Wrong password");
    }
  }catch(err){
    console.error(err);
    showToast("Network error.");
  }
}

async function adminRandomNews(){
  const pass = $("adminPass")?.value || "";
  try{
    const r = await fetch("/api/admin/random", {
      method:"POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify({ password: pass })
    });
    const out = await r.json().catch(()=>({ ok:false, error:"Failed" }));
    showToast(out.ok ? "Random news triggered" : (out.error || "Failed"));
  }catch(err){
    console.error(err);
    showToast("Network error.");
  }
}

async function adminReset(){
  const pass = $("adminPass")?.value || "";
  try{
    const r = await fetch("/api/admin/reset", {
      method:"POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify({ password: pass })
    });
    const out = await r.json().catch(()=>({ ok:false, error:"Failed" }));
    showToast(out.ok ? "Game reset" : (out.error || "Failed"));
  }catch(err){
    console.error(err);
    showToast("Network error.");
  }
}

/* =========================
   Page init (fixed)
   ========================= */
window.addEventListener("DOMContentLoaded", async ()=>{
  const path = window.location.pathname || "/";
  const isAdminPage = path.startsWith("/admin");
  const isGamePage = path.startsWith("/game");
  const isPresenterPage = path.startsWith("/presenter");
  const isHomePage = path === "/" || path === "";

  // Fix home page join behavior
  if(isHomePage){
    setupJoinPage();
    // IMPORTANT: do not run market polling on home page
    return;
  }

  // Shared UI event bindings (only if elements exist)
  if($("readMoreBtn")) $("readMoreBtn").addEventListener("click", openNewsModal);
  if($("closeModalBtn")) $("closeModalBtn").addEventListener("click", closeNewsModal);
  if($("newsModal")){
    $("newsModal").addEventListener("click", (e)=>{
      if(e.target && e.target.id === "newsModal") closeNewsModal();
    });
  }

  // Create chart modal lazily only on pages that may use it
  if(isGamePage || isPresenterPage){
    ensureChartModal();
  }

  // Bootstrap only on pages that need market/company data
  if(isGamePage || isPresenterPage || isAdminPage){
    await bootstrap();
  }

  if(isAdminPage || window.NMG_ADMIN){
    $("loginBtn")?.addEventListener("click", adminLogin);
    $("randomNewsBtn")?.addEventListener("click", adminRandomNews);
    $("resetBtn")?.addEventListener("click", adminReset);
    $("newsSearch")?.addEventListener("input", renderNewsTable);

    await adminLoadNews();
    await adminPoll();
    setInterval(adminPoll, 1000);
    return;
  }

  if(isGamePage || isPresenterPage){
    await pollState();
    setInterval(pollState, 1000);
  }
});