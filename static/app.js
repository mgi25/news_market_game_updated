function $(id){ return document.getElementById(id); }

function fmtMoney(x){
  if (x === null || x === undefined) return "—";
  return Number(x).toLocaleString(undefined, {maximumFractionDigits: 2});
}
function fmtPrice(x){
  if (x === null || x === undefined) return "—";
  return Number(x).toLocaleString(undefined, {maximumFractionDigits: 4});
}

let COMPANIES = [];
let SECTORS = [];
let lastPrices = {};
let qtyDraft = {};
let currentNews = null;
let adminAuthed = false;

function showToast(msg){
  const el = $("toast");
  if(!el) return;
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(window.__toastT);
  window.__toastT = setTimeout(()=>{ el.style.display="none"; }, 1800);
}

function secondsToMMSS(s){
  if (s === null || s === undefined) return "—";
  s = Math.max(0, Math.floor(s));
  const m = Math.floor(s/60);
  const r = s%60;
  return `${m}:${String(r).padStart(2,"0")}`;
}

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
      <td class="right mono" id="px-${c.ticker}">—</td>
      <td class="right mono" id="mv-${c.ticker}">—</td>
      <td class="right">
        <input class="qtyInput" id="qty-${c.ticker}" type="number" min="1" step="1" value="1">
      </td>
      <td class="right">
        <div class="actions">
          <button class="btn small" id="buy-${c.ticker}">Buy</button>
          <button class="btn small ghost" id="sell-${c.ticker}">Sell</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);

    const qtyEl = $(`qty-${c.ticker}`);
    qtyEl.addEventListener("input", ()=>{
      const v = parseInt(qtyEl.value || "1", 10);
      qtyDraft[c.ticker] = (isFinite(v) && v > 0) ? v : 1;
    });

    $(`buy-${c.ticker}`).addEventListener("click", ()=> doTrade(c.ticker, "BUY"));
    $(`sell-${c.ticker}`).addEventListener("click", ()=> doTrade(c.ticker, "SELL"));
  }

  $("search")?.addEventListener("input", applyFilters);
  $("sectorFilter")?.addEventListener("change", applyFilters);
  applyFilters();
}

async function bootstrap(){
  const r = await fetch("/api/bootstrap");
  const data = await r.json();
  COMPANIES = data.companies;
  SECTORS = data.sectors;

  for(const c of COMPANIES){
    lastPrices[c.ticker] = c.start_price;
  }

  fillSectorFilter();
  buildMarketTable();
}

function openNewsModal(){
  if(!currentNews) return;
  $("modalTitle").textContent = currentNews.headline || "";
  $("modalBody").textContent = currentNews.body || "";
  const ul = $("modalBullets");
  ul.innerHTML = "";
  (currentNews.bullets || []).forEach(b=>{
    const li = document.createElement("li");
    li.textContent = b;
    ul.appendChild(li);
  });
  $("newsModal").style.display = "flex";
  $("newsModal").setAttribute("aria-hidden","false");
}
function closeNewsModal(){
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

async function doTrade(ticker, side){
  const player = window.NMG_PLAYER;
  if(!player){
    showToast("Missing player.");
    return;
  }
  const q = qtyDraft[ticker] || parseInt($(`qty-${ticker}`)?.value || "1", 10) || 1;

  const r = await fetch("/api/trade", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({player, ticker, side, qty: q})
  });
  const data = await r.json();
  if(!data.ok){
    showToast(data.error || "Trade failed");
    return;
  }
  showToast(`${side} ${ticker} × ${q}`);
}

function renderHoldings(holdings, prices){
  const body = $("holdingsBody");
  if(!body) return;
  const rows = Object.keys(holdings || {});
  if(rows.length === 0){
    body.innerHTML = `<tr><td colspan="6" class="muted">No holdings yet.</td></tr>`;
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
      <td class="mono">${idx+1}</td>
      <td>${x.player}</td>
      <td class="right mono"><b>${fmtMoney(x.total)}</b></td>
    `;
    body.appendChild(tr);
  });
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
      <td class="right mono ${cls}">${sign}${(m.pct*100).toFixed(2)}%</td>
    `;
    body.appendChild(tr);
  }
}

function updateMarketCells(prices){
  for(const t in prices){
    const px = prices[t];
    const last = lastPrices[t] ?? px;
    lastPrices[t] = px;

    const pct = last === 0 ? 0 : (px - last) / last;
    const cls = pct >= 0 ? "moveUp" : "moveDown";
    const sign = pct >= 0 ? "+" : "";

    const pxEl = $(`px-${t}`);
    const mvEl = $(`mv-${t}`);
    if(pxEl) pxEl.textContent = fmtPrice(px);
    if(mvEl){
      mvEl.textContent = `${sign}${(pct*100).toFixed(2)}%`;
      mvEl.className = `right mono ${cls}`;
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

async function pollState(){
  const player = window.NMG_PLAYER;
  const presenter = window.NMG_PRESENTER;

  let url = "/api/state";
  if(player) url += `?player=${encodeURIComponent(player)}`;
  const r = await fetch(url);
  const s = await r.json();

  $("roundNo") && ($("roundNo").textContent = s.round);
  $("statusText") && ($("statusText").textContent = s.status);
  $("timerText") && ($("timerText").textContent = secondsToMMSS(s.timer_s));

  setNews(s.news || null);
  updateMarketCells(s.prices || {});

  if(player){
    $("cashText").textContent = fmtMoney(s.portfolio.cash);
    $("holdingsText").textContent = fmtMoney(s.portfolio.holdings_value);
    $("totalText").textContent = fmtMoney(s.portfolio.total_value);
    renderHoldings(s.portfolio.holdings, s.prices);
  }
  renderLeaderboard(s.leaderboard);

  if(presenter){
    renderMovers(s.movers);
  }
}

async function adminPoll(){
  const r = await fetch("/api/admin/state");
  const s = await r.json();
  $("roundNo") && ($("roundNo").textContent = s.round);
  $("statusText") && ($("statusText").textContent = s.status);
  $("timerText") && ($("timerText").textContent = secondsToMMSS(s.timer_s));
  $("headlineText") && ($("headlineText").textContent = s.headline || "—");
}

function scopeLabel(n){
  if((n.tickers||[]).length) return "Company";
  const secs = (n.sectors||[]);
  return secs.length > 1 ? "Multi" : "Sector";
}

async function adminLoadNews(){
  const r = await fetch("/api/admin/news");
  const data = await r.json();
  window.__NEWS = data.news;
  renderNewsTable();
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
      <td class="right"><button class="btn small" data-id="${n.id}">Trigger</button></td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll("button[data-id]").forEach(btn=>{
    btn.addEventListener("click", async ()=>{
      if(!adminAuthed) return showToast("Unlock admin first");
      const id = btn.getAttribute("data-id");
      const pass = $("adminPass").value || "";
      const rr = await fetch("/api/admin/trigger", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({password: pass, news_id: id})
      });
      const out = await rr.json();
      showToast(out.ok ? "News triggered" : (out.error || "Failed"));
    });
  });
}

async function adminLogin(){
  const pass = $("adminPass").value || "";
  const r = await fetch("/api/admin/login", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({password: pass})
  });
  const out = await r.json();
  if(out.ok){
    adminAuthed = true;
    $("randomNewsBtn").disabled = false;
    $("resetBtn").disabled = false;
    showToast("Admin unlocked");
  } else {
    showToast("Wrong password");
  }
}

async function adminRandomNews(){
  const pass = $("adminPass").value || "";
  const r = await fetch("/api/admin/random", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({password: pass})
  });
  const out = await r.json();
  showToast(out.ok ? "Random news triggered" : (out.error || "Failed"));
}

async function adminReset(){
  const pass = $("adminPass").value || "";
  const r = await fetch("/api/admin/reset", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({password: pass})
  });
  const out = await r.json();
  showToast(out.ok ? "Game reset" : (out.error || "Failed"));
}

window.addEventListener("DOMContentLoaded", async ()=>{
  await bootstrap();

  if($("readMoreBtn")){
    $("readMoreBtn").addEventListener("click", openNewsModal);
  }
  if($("closeModalBtn")){
    $("closeModalBtn").addEventListener("click", closeNewsModal);
  }
  if($("newsModal")){
    $("newsModal").addEventListener("click", (e)=>{
      if(e.target && e.target.id === "newsModal") closeNewsModal();
    });
  }

  if(window.NMG_ADMIN){
    $("loginBtn").addEventListener("click", adminLogin);
    $("randomNewsBtn").addEventListener("click", adminRandomNews);
    $("resetBtn").addEventListener("click", adminReset);
    $("newsSearch").addEventListener("input", renderNewsTable);
    await adminLoadNews();
    setInterval(adminPoll, 1000);
  } else {
    await pollState();
    setInterval(pollState, 1000);
  }
});
