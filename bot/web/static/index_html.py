\
HTML = r'''
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trading Bot Dashboard</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; }
    h1 { margin-top: 0; }
    .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 24px; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; }
    .footer { color: #666; font-size: 12px; margin-top: 12px; }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
  <h1>Trading Bot Dashboard</h1>
  <div class="grid">
    <div class="card">
      <h3>Equity</h3>
      <canvas id="equityChart" height="120"></canvas>
    </div>
    <div class="card">
      <h3>Recent Trades</h3>
      <table id="tradesTable">
        <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>P&amp;L</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
  <div class="footer">Local Paper Bot • FastAPI • SQLite • Logs rotate in <code>logs/</code></div>
<script>
async function loadEquity() {
  const res = await fetch('/equity?limit=500');
  const data = await res.json();
  const labels = data.map(d => new Date(d.ts));
  const values = data.map(d => d.equity);
  const ctx = document.getElementById('equityChart').getContext('2d');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{ label: 'Equity', data: values }]
    },
    options: {
      responsive: true,
      scales: { x: { type: 'time', time: { unit: 'minute' } } }
    }
  });
}

async function loadTrades() {
  const res = await fetch('/trades?limit=100');
  const rows = await res.json();
  const tbody = document.querySelector('#tradesTable tbody');
  tbody.innerHTML = '';
  rows.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${new Date(r.ts).toLocaleString()}</td>
                    <td>${r.symbol}</td>
                    <td>${r.side.toUpperCase()}</td>
                    <td>${r.qty.toFixed(4)}</td>
                    <td>$${r.price.toFixed(2)}</td>
                    <td>${r.pnl.toFixed(2)}</td>`;
    tbody.appendChild(tr);
  });
}

loadEquity();
loadTrades();
setInterval(() => { loadEquity(); loadTrades(); }, 60_000);
</script>
</body>
</html>
'''
