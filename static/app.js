const toast = document.getElementById('toast');
const fmt = (n)=> Number(n||0).toLocaleString(undefined,{maximumFractionDigits:2});
function showToast(msg){ if(!toast) return; toast.textContent=msg; toast.style.display='block'; setTimeout(()=>toast.style.display='none',2200); }

async function api(url, options={}){
  const res = await fetch(url,{headers:{'Content-Type':'application/json'},...options});
  const data = await res.json().catch(()=>({ok:false,error:'Invalid server response'}));
  if(!res.ok || !data.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function marketBadge(el, open){ if(!el) return; el.textContent=open?'Market Open':'Market Closed'; el.classList.toggle('open',open); el.classList.toggle('closed',!open); }

async function gamePage(){
  const qs = {
    round:document.getElementById('roundLabel'), badge:document.getElementById('marketBadge'), timer:document.getElementById('timerLabel'),
    newsList:document.getElementById('newsList'), prices:document.getElementById('pricesBody'), ticker:document.getElementById('tradeTicker'),
    cash:document.getElementById('cash'), hv:document.getElementById('holdingsValue'), pnl:document.getElementById('pnl'), hold:document.getElementById('holdings'), tx:document.getElementById('tx'), lb:document.getElementById('leaderboard')
  };
  const modal = document.getElementById('newsModal');
  const modalTitle = document.getElementById('modalTitle');
  const modalBody = document.getElementById('modalBody');
  document.getElementById('modalClose')?.addEventListener('click',()=> modal.style.display='none');

  async function trade(side){
    try{
      await api('/api/trade',{method:'POST',body:JSON.stringify({ticker:qs.ticker.value,qty:Number(document.getElementById('tradeQty').value||1),side})});
      showToast('Trade executed');
      await refresh();
    }catch(e){ showToast(e.message); }
  }
  document.getElementById('buyBtn')?.addEventListener('click',()=>trade('BUY'));
  document.getElementById('sellBtn')?.addEventListener('click',()=>trade('SELL'));

  async function refresh(){
    try{
      const data = await api('/api/state');
      const s = data.state;
      qs.round.textContent = `Round ${s.round}/${s.max_rounds}`;
      qs.timer.textContent = `${s.timer}s`;
      marketBadge(qs.badge, s.market_open);

      qs.newsList.innerHTML='';
      if(s.news){
        const card=document.createElement('div'); card.className='news-item';
        card.innerHTML=`<strong>${s.news.headline}</strong><p class='muted'>${s.news.description}</p><button>Read more</button>`;
        card.querySelector('button').onclick=()=>{ modalTitle.textContent=s.news.headline; modalBody.textContent=s.news.body; modal.style.display='flex'; };
        qs.newsList.appendChild(card);
      }

      qs.prices.innerHTML=''; qs.ticker.innerHTML='';
      s.prices.forEach(row=>{
        const dir = row.change_pct >=0 ? 'up':'down';
        qs.prices.insertAdjacentHTML('beforeend',`<tr><td>${row.ticker}</td><td>${row.name}</td><td>${row.sector}</td><td>${fmt(row.price)}</td><td class='${dir}'>${row.change_pct>=0?'▲':'▼'} ${row.change_pct}%</td></tr>`);
        qs.ticker.insertAdjacentHTML('beforeend',`<option value='${row.ticker}'>${row.ticker}</option>`);
      });

      const p=s.portfolio;
      qs.cash.textContent=`Cash: $${fmt(p.cash)}`;
      qs.hv.textContent=`Holdings: $${fmt(p.holdings_value)}`;
      qs.pnl.textContent=`PnL (R/U): $${fmt(p.realized_pnl)} / $${fmt(p.unrealized_pnl)}`;

      qs.hold.innerHTML = Object.entries(p.holdings).length ? Object.entries(p.holdings).map(([t,h])=>`${t}: ${h.qty} @ ${fmt(h.avg_cost)}`).join('<br>') : 'No holdings';
      qs.tx.innerHTML = p.transactions.length ? p.transactions.slice().reverse().map(x=>`${x.side} ${x.ticker} x${x.qty} @ ${fmt(x.price)}`).join('<br>') : 'No trades yet';
      qs.lb.innerHTML = s.leaderboard.map((x,i)=>`${i+1}. ${x.name} — $${fmt(x.total)}`).join('<br>');

      if(s.game_over) showToast('Game ended. Final leaderboard locked.');
    }catch(e){ showToast(e.message); }
  }
  await refresh();
  setInterval(refresh, 1000);
}

async function adminPage(){
  document.getElementById('adminLogin')?.addEventListener('click', async ()=>{
    try{ await api('/api/admin/login',{method:'POST',body:JSON.stringify({password:document.getElementById('adminPassword').value})}); showToast('Admin logged in'); refresh(); }
    catch(e){ showToast(e.message); }
  });
  document.getElementById('saveWindow')?.addEventListener('click', async ()=>{
    try{ await api('/api/admin/reaction_window',{method:'POST',body:JSON.stringify({seconds:Number(document.getElementById('reactionWindow').value)})}); showToast('Window saved'); }
    catch(e){ showToast(e.message); }
  });
  document.getElementById('startGame')?.addEventListener('click', async ()=>{ try{ await api('/api/admin/start',{method:'POST'}); showToast('Game started'); refresh(); }catch(e){showToast(e.message);} });
  document.getElementById('advanceRound')?.addEventListener('click', async ()=>{ try{ await api('/api/admin/advance_round',{method:'POST'}); showToast('Round advanced'); refresh(); }catch(e){showToast(e.message);} });
  document.getElementById('resetGame')?.addEventListener('click', async ()=>{ try{ await api('/api/admin/reset',{method:'POST'}); showToast('Reset complete'); refresh(); }catch(e){showToast(e.message);} });

  async function refresh(){
    try{
      const data=await api('/api/admin/state');
      const s=data.state;
      document.getElementById('adminState').innerHTML=`Round ${s.round}/${s.max_rounds} · ${s.market_open?'OPEN':'CLOSED'} · ${s.timer}s`;
      document.getElementById('adminPlayers').innerHTML=data.players.map(p=>`${p.name} ($${fmt(p.cash)})`).join('<br>') || 'No players';
      document.getElementById('adminPrices').innerHTML=s.prices.map(p=>`${p.ticker}: $${fmt(p.price)} (${p.change_pct}%)`).join('<br>');
      document.getElementById('reactionWindow').value=data.reaction_window;
    }catch(e){/* not logged in yet */}
  }
  setInterval(refresh,1000); refresh();
}

async function presenterPage(){
  async function refresh(){
    try{
      const data=await api('/api/presenter/state'); const s=data.state;
      document.getElementById('presenterRound').textContent=`Round ${s.round}/${s.max_rounds}`;
      document.getElementById('presenterTimer').textContent=`${s.timer}s`;
      marketBadge(document.getElementById('presenterMarket'), s.market_open);
      document.getElementById('presenterHeadline').textContent=s.news?.headline || 'Waiting for news...';
      document.getElementById('presenterBody').textContent=s.news?.body || '';
      document.getElementById('presenterMovers').innerHTML=(data.movers||[]).map(x=>`${x.ticker}: ${x.change_pct}%`).join('<br>');
      document.getElementById('presenterBoard').innerHTML=s.leaderboard.map((x,i)=>`${i+1}. ${x.name} — $${fmt(x.total)}`).join('<br>');
    }catch(e){ showToast(e.message); }
  }
  refresh(); setInterval(refresh,1000);
}

if(window.PAGE==='game') gamePage();
if(window.PAGE==='admin') adminPage();
if(window.PAGE==='presenter') presenterPage();
