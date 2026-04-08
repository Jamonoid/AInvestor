/**
 * AutoInvest - Dashboard Frontend
 * Fetch de APIs, renderizado de datos y WebSocket para tiempo real.
 */

const API = '';  // mismo host

// --------------------------------------------------------
// State
// --------------------------------------------------------
let state = {
    status: {},
    portfolio: {},
    tickers: [],
    analysis: {},
    sentiment: {},
    trades: [],
    agent: {},
};

// --------------------------------------------------------
// Helpers
// --------------------------------------------------------
function $(id) { return document.getElementById(id); }

function fmt(n, decimals = 2) {
    if (n == null || isNaN(n)) return '--';
    return Number(n).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

function fmtUSD(n) {
    if (n == null || isNaN(n)) return '$--';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1_000_000) return `${sign}$${fmt(abs / 1_000_000)}M`;
    if (abs >= 1_000) return `${sign}$${fmt(abs)}`;
    return `${sign}$${fmt(abs, abs < 1 ? 6 : 2)}`;
}

function pnlClass(val) {
    if (val > 0) return 'positive';
    if (val < 0) return 'negative';
    return 'neutral';
}

function signalClass(signal) {
    if (signal === 'buy') return 'buy';
    if (signal === 'sell') return 'sell';
    return 'neutral';
}

function timeAgo(isoStr) {
    if (!isoStr) return '--';
    const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
    if (diff < 60) return `hace ${Math.floor(diff)}s`;
    if (diff < 3600) return `hace ${Math.floor(diff / 60)}min`;
    if (diff < 86400) return `hace ${Math.floor(diff / 3600)}h`;
    return `hace ${Math.floor(diff / 86400)}d`;
}

// --------------------------------------------------------
// API Fetchers
// --------------------------------------------------------
async function fetchJSON(url) {
    try {
        const resp = await fetch(API + url);
        if (!resp.ok) throw new Error(resp.statusText);
        return await resp.json();
    } catch (err) {
        console.error(`Error fetching ${url}:`, err);
        return null;
    }
}

async function refreshAll() {
    const [status, portfolio, tickers, analysis, sentiment, trades, agent] = await Promise.all([
        fetchJSON('/api/status'),
        fetchJSON('/api/portfolio'),
        fetchJSON('/api/tickers'),
        fetchJSON('/api/analysis'),
        fetchJSON('/api/sentiment'),
        fetchJSON('/api/trades?limit=20'),
        fetchJSON('/api/agent'),
    ]);

    if (status) state.status = status;
    if (portfolio) state.portfolio = portfolio;
    if (tickers) state.tickers = tickers;
    if (analysis) state.analysis = analysis;
    if (sentiment) state.sentiment = sentiment;
    if (trades) state.trades = trades;
    if (agent) state.agent = agent;

    render();
}

// --------------------------------------------------------
// Renderers
// --------------------------------------------------------
function render() {
    renderStatus();
    renderTickers();
    renderKPIs();
    renderSentiment();
    renderAgent();
    renderAnalysis();
    renderPositions();
    renderTrades();
}

function renderStatus() {
    const s = state.status;
    const badge = $('statusBadge');
    const text = $('statusText');

    if (s.mode === 'paper') {
        badge.className = 'status-badge paper';
        text.textContent = 'PAPER';
    } else if (s.mode === 'live') {
        badge.className = 'status-badge live';
        text.textContent = 'LIVE';
    }

    $('lastUpdate').textContent = s.last_update ? `Actualizado ${timeAgo(s.last_update)}` : 'Sin datos';
    $('riskLevel').textContent = `Riesgo: ${s.risk_level || '--'}`;
}

function renderTickers() {
    const strip = $('tickerStrip');
    if (!state.tickers.length) return;

    strip.innerHTML = state.tickers.map(t => {
        const changeClass = (t.change_24h_pct || 0) >= 0 ? 'up' : 'down';
        const changeSign = (t.change_24h_pct || 0) >= 0 ? '+' : '';
        const sym = (t.symbol || '').replace('/USDT', '');
        return `
            <div class="ticker-item fade-in">
                <div class="ticker-symbol">${sym}</div>
                <div class="ticker-price">${fmtUSD(t.price)}</div>
                <div class="ticker-change ${changeClass}">${changeSign}${fmt(t.change_24h_pct)}%</div>
            </div>
        `;
    }).join('');
}

function renderKPIs() {
    const p = state.portfolio;
    const totalValue = p.total_value ?? p.cash ?? 10000;
    const pnl = p.total_pnl ?? 0;
    const pnlPct = p.total_pnl_pct ?? 0;
    const cash = p.cash ?? totalValue;
    const numPos = p.num_positions ?? Object.keys(p.positions || {}).length;

    $('kpiTotalValue').textContent = fmtUSD(totalValue);
    $('kpiTotalValue').className = `kpi-value ${pnlClass(pnl)}`;

    $('kpiPnl').textContent = `${pnl >= 0 ? '+' : ''}${fmtUSD(pnl)}`;
    $('kpiPnl').className = `kpi-value ${pnlClass(pnl)}`;
    $('kpiPnlPct').textContent = `${pnlPct >= 0 ? '+' : ''}${fmt(pnlPct)}%`;

    $('kpiCash').textContent = fmtUSD(cash);
    $('kpiPositions').textContent = `${numPos} posicion${numPos !== 1 ? 'es' : ''}`;

    // Fear & Greed
    const fng = state.sentiment?.fear_greed;
    if (fng && fng.current_value != null) {
        $('kpiFearGreed').textContent = fng.current_value;
        $('kpiFearGreedLabel').textContent = fng.current_label || '';
        const val = fng.current_value;
        let color = 'var(--yellow)';
        if (val <= 25) color = 'var(--red)';
        else if (val <= 45) color = '#ff8844';
        else if (val >= 75) color = 'var(--green)';
        else if (val >= 55) color = 'var(--green-dim)';
        $('kpiFearGreed').style.color = color;
    }
}

function renderSentiment() {
    const s = state.sentiment;
    const fng = s?.fear_greed;

    if (fng && fng.current_value != null) {
        $('sentimentValue').textContent = fng.current_value;
        $('sentimentLabel').textContent = fng.current_label || '';

        const pct = Math.max(0, Math.min(100, fng.current_value));
        $('sentimentIndicator').style.left = `calc(${pct}% - 8px)`;

        let color = 'var(--yellow)';
        if (pct <= 25) color = 'var(--red)';
        else if (pct >= 75) color = 'var(--green)';
        $('sentimentValue').style.color = color;
        $('sentimentLabel').style.color = color;
    }
}

function renderAgent() {
    const a = state.agent;

    // Outlook
    const outlook = a?.outlook || 'El agente aun no ha tomado decisiones. Esperando datos del mercado...';
    $('agentOutlook').textContent = outlook;

    // Decisions
    const container = $('agentDecisions');
    const decisions = a?.last_decision?.decisions || [];

    if (!decisions.length) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = decisions.map(d => {
        const actionClass = d.action === 'BUY' ? 'buy' : d.action === 'SELL' ? 'sell' : 'neutral';
        const bgClass = d.action === 'BUY' ? 'background:var(--green-glow);color:var(--green);' :
                         d.action === 'SELL' ? 'background:var(--red-glow);color:var(--red);' :
                         'background:rgba(136,136,168,0.1);color:var(--text-secondary);';
        const confColor = d.confidence >= 70 ? 'var(--green)' : d.confidence >= 50 ? 'var(--yellow)' : 'var(--text-secondary)';

        return `
            <div class="agent-decision fade-in">
                <div class="action-badge" style="${bgClass}">${d.action || 'HOLD'}</div>
                <div style="flex:1">
                    <div style="font-weight:600;font-size:0.85rem">${d.symbol || '--'}</div>
                    <div class="reasoning">${d.reasoning || ''}</div>
                </div>
                <div class="confidence" style="color:${confColor}">${d.confidence || 0}%</div>
            </div>
        `;
    }).join('');
}

function renderAnalysis() {
    const container = $('technicalAnalysis');
    const analysis = state.analysis;
    const symbols = Object.keys(analysis);

    if (!symbols.length) return;

    let html = '';
    for (const sym of symbols) {
        const data = analysis[sym];
        const signal = data.overall_signal || 'neutral';
        const score = data.overall_score || 0;
        const scoreColor = signal === 'buy' ? 'var(--green)' : signal === 'sell' ? 'var(--red)' : 'var(--text-secondary)';
        const scorePct = ((score + 1) / 2) * 100; // -1..1 -> 0..100
        const barColor = signal === 'buy' ? 'var(--green)' : signal === 'sell' ? 'var(--red)' : 'var(--text-muted)';

        html += `
            <div style="margin-bottom:16px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <span style="font-weight:600;font-size:0.9rem">${sym.replace('/USDT','')}</span>
                    <span class="signal-badge ${signalClass(signal)}">${signal.toUpperCase()}</span>
                </div>
                <div class="score-bar-container">
                    <div class="score-bar">
                        <div class="score-bar-fill" style="width:${scorePct}%;background:${barColor}"></div>
                    </div>
                </div>
                <div style="margin-top:6px">
        `;

        for (const sig of (data.signals || [])) {
            html += `
                <div class="signal-item">
                    <span class="signal-name">${sig.name}</span>
                    <span class="signal-badge ${signalClass(sig.signal)}">${sig.signal.toUpperCase()}</span>
                </div>
            `;
        }

        html += '</div></div>';
    }

    container.innerHTML = html;
}

function renderPositions() {
    const container = $('positionsContainer');
    const positions = state.portfolio?.positions || {};
    const symbols = Object.keys(positions);

    if (!symbols.length) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="icon">&#128230;</div>
                <p>Sin posiciones abiertas</p>
            </div>
        `;
        return;
    }

    let html = `
        <table class="positions-table">
            <thead>
                <tr>
                    <th>Par</th>
                    <th>Cantidad</th>
                    <th>Entrada</th>
                    <th>Actual</th>
                    <th>PnL</th>
                </tr>
            </thead>
            <tbody>
    `;

    for (const [sym, pos] of Object.entries(positions)) {
        const pnlPct = pos.unrealized_pnl_pct ?? pos.pnl_percent ?? 0;
        const cls = pnlClass(pnlPct);
        const color = cls === 'positive' ? 'var(--green)' : cls === 'negative' ? 'var(--red)' : 'var(--text-primary)';

        html += `
            <tr>
                <td style="font-weight:600">${sym.replace('/USDT','')}</td>
                <td>${fmt(pos.amount, 6)}</td>
                <td>${fmtUSD(pos.entry_price)}</td>
                <td>${fmtUSD(pos.current_price)}</td>
                <td style="color:${color};font-weight:600">${pnlPct >= 0 ? '+' : ''}${fmt(pnlPct)}%</td>
            </tr>
        `;
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

function renderTrades() {
    const container = $('tradeHistory');
    const trades = state.trades;

    if (!trades.length) return;

    container.innerHTML = trades.map(t => {
        const pnlColor = t.pnl > 0 ? 'var(--green)' : t.pnl < 0 ? 'var(--red)' : 'var(--text-secondary)';
        return `
            <div class="trade-item fade-in">
                <div class="trade-side ${t.side}">${t.side.toUpperCase()}</div>
                <div class="trade-details">
                    <div class="trade-symbol">${t.symbol}</div>
                    <div class="trade-meta">${fmt(t.amount, 6)} @ ${fmtUSD(t.price)} &middot; ${timeAgo(t.timestamp)}</div>
                </div>
                <div class="trade-pnl" style="color:${pnlColor}">
                    ${t.pnl !== 0 ? (t.pnl > 0 ? '+' : '') + fmtUSD(t.pnl) : fmtUSD(t.cost)}
                </div>
            </div>
        `;
    }).join('');
}

// --------------------------------------------------------
// WebSocket
// --------------------------------------------------------
function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'state_update' && msg.data) {
                if (msg.data.portfolio) state.portfolio = msg.data.portfolio;
                if (msg.data.tickers) state.tickers = msg.data.tickers;
                if (msg.data.bot_running != null) state.status.bot_running = msg.data.bot_running;
                if (msg.data.last_update) state.status.last_update = msg.data.last_update;
                if (msg.data.risk_level) state.status.risk_level = msg.data.risk_level;
                render();
            }
        } catch (e) {
            console.error('WS parse error:', e);
        }
    };

    ws.onclose = () => {
        console.log('WS disconnected, reconnecting in 5s...');
        setTimeout(connectWS, 5000);
    };

    ws.onerror = () => ws.close();
}

// --------------------------------------------------------
// Init
// --------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    refreshAll();
    setInterval(refreshAll, 15000);  // refresh cada 15 segundos
    connectWS();
});
