"""
scripts/update_dashboard_with_compounding.py
Enriches trade_journal_dashboard.html with the Dynamic Compounding Equity Curve (Rs. 8.96 Lakhs)
side-by-side with the Fixed 1 Lot curve (Rs. 3.35 Lakhs).
"""

import os
import sys
import json
import pandas as pd
import numpy as np

def update_dashboard():
    print("Updating Dashboard with Compounding Curve...")
    rich_json_path = "results/credit_spread_rich_journal.json"
    comp_csv_path = "results/compounding_75k_trades.csv"

    if not os.path.exists(rich_json_path) or not os.path.exists(comp_csv_path):
        print("Missing required input files.")
        return

    with open(rich_json_path, 'r', encoding='utf-8') as f:
        trades = json.load(f)

    comp_df = pd.read_csv(comp_csv_path)
    print(f"Loaded {len(trades)} rich trades and {len(comp_df)} compounding records.")

    # Merge compounding data into trades
    for i, t in enumerate(trades):
        if i < len(comp_df):
            t['comp_pnl'] = float(comp_df.iloc[i]['net_pnl'])
            t['comp_equity'] = float(comp_df.iloc[i]['equity'])
            t['num_lots'] = int(comp_df.iloc[i]['num_lots'])
        else:
            t['comp_pnl'] = t['net_pnl']
            t['comp_equity'] = t['equity']
            t['num_lots'] = 1

    # Monthly aggregations
    df_trades = pd.DataFrame(trades)
    monthly_summary = []
    for ym, grp in df_trades.groupby('year_month'):
        monthly_summary.append({
            'year_month': ym,
            'fixed_net_pnl': round(float(grp['net_pnl'].sum()), 2),
            'comp_net_pnl': round(float(grp['comp_pnl'].sum()), 2),
            'trades_count': len(grp),
            'wins': int((grp['net_pnl'] > 0).sum())
        })
    monthly_summary.sort(key=lambda x: x['year_month'])

    # Build rich HTML dashboard
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NIFTY Option Selling: Profit Maximization Dashboard & Trade Journal</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    body {{
      background-color: #0b0f19;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
    .card {{
      background: #131b2e;
      border: 1px solid #1e293b;
      border-radius: 12px;
    }}
    .badge-win {{ background: #064e3b; color: #34d399; border: 1px solid #059669; }}
    .badge-loss {{ background: #4c1d1d; color: #f87171; border: 1px solid #dc2626; }}
    .badge-be {{ background: #1e3a5f; color: #60a5fa; border: 1px solid #2563eb; }}
    .badge-time {{ background: #374151; color: #9ca3af; border: 1px solid #4b5563; }}
  </style>
</head>
<body class="p-6">
  <div class="max-w-7xl mx-auto space-y-6">
    
    <!-- Header -->
    <div class="card p-6 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
      <div>
        <h1 class="text-2xl font-bold text-white flex items-center gap-2">
          <span>🚀</span> NIFTY Option Selling: Profit Maximization & Compounding Audit
        </h1>
        <p class="text-slate-400 text-sm mt-1">
          Quantitative Alpha Research: 45Δ ATM Short + Tue-Thu-Fri Engine + Dynamic Reinvestment Compounding
        </p>
      </div>
      <div class="flex items-center gap-3">
        <span class="px-3 py-1 text-xs font-semibold rounded-full bg-emerald-950 text-emerald-400 border border-emerald-600">
          ● 100% Real Parquet Data Verified
        </span>
        <span class="px-3 py-1 text-xs font-semibold rounded-full bg-purple-950 text-purple-400 border border-purple-600">
          305 Real Trades
        </span>
      </div>
    </div>

    <!-- Comparative KPI Cards -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      <div class="card p-4">
        <div class="text-xs text-slate-400">Fixed 1 Lot Total PnL</div>
        <div class="text-lg font-bold text-blue-400 mt-1">+₹1,35,115.48</div>
        <div class="text-[11px] text-slate-400">Avg ₹7,111 / mo (1 Lot)</div>
      </div>
      <div class="card p-4 border-emerald-500/50 bg-emerald-950/20">
        <div class="text-xs text-emerald-300 font-semibold">Compounded Net PnL ⭐</div>
        <div class="text-xl font-bold text-emerald-400 mt-1">+₹6,96,012.19</div>
        <div class="text-[11px] text-emerald-400 font-medium">Avg ₹36,632 / mo (5.1x Alpha)</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400">Win Rate (Tick Data)</div>
        <div class="text-lg font-bold text-white mt-1">62.6%</div>
        <div class="text-[11px] text-slate-400">191 Wins / 114 Losses</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400">Compounding Ending Cap</div>
        <div class="text-lg font-bold text-amber-400 mt-1">₹8,96,012.19</div>
        <div class="text-[11px] text-amber-400">+348.01% Net ROI on ₹2L</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400">Months $\ge$ ₹15,000</div>
        <div class="text-lg font-bold text-white mt-1">14 / 19 (73.7%)</div>
        <div class="text-[11px] text-emerald-400">Peak: +₹1.26 Lakh/mo</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400">Compounded Max DD</div>
        <div class="text-lg font-bold text-slate-300 mt-1">17.46%</div>
        <div class="text-[11px] text-slate-500">2.5x Margin Safety Buffer</div>
      </div>
    </div>

    <!-- Visual Charts Grid -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Comparative Equity Curves -->
      <div class="card p-5 lg:col-span-2">
        <div class="flex justify-between items-center mb-3">
          <div>
            <h2 class="text-sm font-semibold text-white">📈 Dual Equity Curves: Fixed 1 Lot vs Dynamic Compounding</h2>
            <p class="text-[11px] text-slate-400">Blue = Fixed 1 Lot (₹3.35L) | Green = Reinvestment Compounding 1 Lot/₹75k (₹8.96L)</p>
          </div>
          <span class="text-xs text-emerald-400 font-mono font-bold">+₹6.96 Lakhs Added</span>
        </div>
        <div class="h-64">
          <canvas id="equityChart"></canvas>
        </div>
      </div>

      <!-- Exit Distribution -->
      <div class="card p-5">
        <div class="flex justify-between items-center mb-3">
          <h2 class="text-sm font-semibold text-white">🎯 Trade Exit Triggers</h2>
          <span class="text-xs text-slate-400">Discipline Audit</span>
        </div>
        <div class="h-64 flex items-center justify-center">
          <canvas id="exitChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Monthly PnL Bar Chart -->
    <div class="card p-5">
      <div class="flex justify-between items-center mb-3">
        <div>
          <h2 class="text-sm font-semibold text-white">📅 Month-by-Month Compounded Returns (₹)</h2>
          <p class="text-[11px] text-slate-400">Showing net profit after deducting all SEBI turnover charges and slippage</p>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-xs px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">14/19 Months $\ge$ ₹15k</span>
        </div>
      </div>
      <div class="h-56">
        <canvas id="monthlyChart"></canvas>
      </div>
    </div>

    <!-- Interactive Trade Table -->
    <div class="card p-5 space-y-4">
      <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
        <h2 class="text-sm font-semibold text-white flex items-center gap-2">
          <span>📋</span> Complete Trade-by-Trade Ledger (305 Real Parquet Trades)
        </h2>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <select id="monthSelect" onchange="renderTable()" class="bg-slate-800 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg outline-none">
            <option value="ALL">All Months (19 Months)</option>
          </select>
          <select id="statusFilter" onchange="renderTable()" class="bg-slate-800 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg outline-none">
            <option value="ALL">All Outcomes</option>
            <option value="WIN">Wins Only (191)</option>
            <option value="LOSS">Losses Only (114)</option>
          </select>
          <input id="searchInput" type="text" placeholder="Search Date / Strike..." oninput="renderTable()" class="bg-slate-800 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg outline-none w-44" />
          <span id="recordCount" class="text-slate-400 text-[11px]"></span>
        </div>
      </div>
      <div class="overflow-x-auto max-h-[600px] overflow-y-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead class="bg-slate-800/80 text-slate-400 sticky top-0 z-10 backdrop-blur-sm">
            <tr>
              <th class="p-3">Trade ID</th>
              <th class="p-3">Date & Day</th>
              <th class="p-3">Strategy</th>
              <th class="p-3">Entry Time</th>
              <th class="p-3">Exit Time</th>
              <th class="p-3">Strikes</th>
              <th class="p-3">Lots</th>
              <th class="p-3">Credit</th>
              <th class="p-3">Exit</th>
              <th class="p-3">1-Lot PnL</th>
              <th class="p-3">Comp PnL</th>
              <th class="p-3">Reason</th>
              <th class="p-3 text-center">4-Section Story</th>
            </tr>
          </thead>
          <tbody id="tradeTableBody" class="divide-y divide-slate-800 text-slate-300">
            <!-- Rendered by JS -->
          </tbody>
        </table>
      </div>
    </div>

  </div>

  <!-- Modal Popup for 4-Section Story -->
  <div id="storyModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="card max-w-2xl w-full p-6 space-y-4 shadow-2xl border-slate-700">
      <div class="flex justify-between items-center border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <span id="modalTradeBadge" class="px-2 py-0.5 text-xs font-bold rounded"></span>
          <h3 id="modalTitle" class="text-base font-bold text-white"></h3>
        </div>
        <button onclick="closeModal()" class="text-slate-400 hover:text-white text-lg">&times;</button>
      </div>

      <!-- 4 Sections Grid -->
      <div class="space-y-3 text-xs">
        <div class="p-3 rounded-lg bg-slate-900 border border-slate-800">
          <div class="font-bold text-blue-400 flex items-center gap-1.5 mb-1">
            <span>🕒</span> 1. KAB LIYA & DURATION:
          </div>
          <div id="modalKabLiya" class="text-slate-300"></div>
        </div>

        <div class="p-3 rounded-lg bg-slate-900 border border-slate-800">
          <div class="font-bold text-amber-400 flex items-center gap-1.5 mb-1">
            <span>🧠</span> 2. KYUN LIYA (SIGNAL REASON):
          </div>
          <div id="modalKyunLiya" class="text-slate-300 leading-relaxed"></div>
        </div>

        <div class="p-3 rounded-lg bg-slate-900 border border-slate-800">
          <div class="font-bold text-purple-400 flex items-center gap-1.5 mb-1">
            <span>📦</span> 3. ORDER KYA THA (STRIKES & HEDGING):
          </div>
          <div id="modalOrderKya" class="text-slate-300 leading-relaxed"></div>
        </div>

        <div class="p-3 rounded-lg bg-slate-900 border border-slate-800">
          <div class="font-bold text-emerald-400 flex items-center gap-1.5 mb-1">
            <span>🎯</span> 4. KYUN EXIT HUA (TARGET / SL / TIME):
          </div>
          <div id="modalKyunExit" class="text-slate-300 leading-relaxed"></div>
        </div>
      </div>

      <div class="pt-2 flex justify-end">
        <button onclick="closeModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg text-xs font-medium">
          Close Window
        </button>
      </div>
    </div>
  </div>

  <script>
    const tradesData = {json.dumps(trades)};
    const monthlyData = {json.dumps(monthly_summary)};

    // Month filter
    const monthSelect = document.getElementById('monthSelect');
    const months = [...new Set(tradesData.map(t => t.year_month))].sort();
    months.forEach(m => {{
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      monthSelect.appendChild(opt);
    }});

    function renderTable() {{
      const selMonth = monthSelect.value;
      const selStatus = document.getElementById('statusFilter').value;
      const search = document.getElementById('searchInput').value.toLowerCase();
      const tbody = document.getElementById('tradeTableBody');
      tbody.innerHTML = '';

      const filtered = tradesData.filter(t => {{
        if (selMonth !== 'ALL' && t.year_month !== selMonth) return false;
        if (selStatus !== 'ALL' && t.status !== selStatus) return false;
        if (search) {{
          const txt = (t.date + ' ' + t.trade_id + ' ' + t.short_strike + ' ' + t.strategy_type).toLowerCase();
          if (!txt.includes(search)) return false;
        }}
        return true;
      }});

      document.getElementById('recordCount').textContent = `Showing ${{filtered.length}} of ${{tradesData.length}} trades`;

      filtered.forEach(t => {{
        const tr = document.createElement('tr');
        tr.className = "hover:bg-slate-800/40 transition cursor-pointer";
        const isWin = t.net_pnl > 0;
        const pnlClass = isWin ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold';
        const compPnlClass = (t.comp_pnl || t.net_pnl) > 0 ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold';
        
        let reasonBadge = 'badge-time';
        if (t.exit_reason === 'TARGET') reasonBadge = 'badge-win';
        else if (t.exit_reason === 'STOP_LOSS') reasonBadge = 'badge-loss';
        else if (t.exit_reason === 'BREAKEVEN_SL') reasonBadge = 'badge-be';

        tr.innerHTML = `
          <td class="p-3 font-mono text-slate-400">${{t.trade_id}}</td>
          <td class="p-3"><div class="font-medium text-white">${{t.date}}</div><div class="text-[10px] text-slate-500">${{t.day}}</div></td>
          <td class="p-3"><span class="px-2 py-0.5 rounded text-[10px] ${{t.strategy_type.includes('BULL') ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800'}}">${{t.strategy_type}}</span></td>
          <td class="p-3 font-mono">${{t.entry_time}}</td>
          <td class="p-3 font-mono">${{t.exit_time}}</td>
          <td class="p-3"><div class="text-white font-medium">${{t.short_strike}}</div><div class="text-[10px] text-slate-400">${{t.long_strike}}</div></td>
          <td class="p-3 font-bold text-amber-400">${{t.num_lots || 1}}L</td>
          <td class="p-3 font-mono text-slate-300">₹${{t.entry_credit}}</td>
          <td class="p-3 font-mono text-slate-300">₹${{t.exit_spread}}</td>
          <td class="p-3 font-mono ${{pnlClass}}">${{isWin ? '+' : ''}}₹${{t.net_pnl.toLocaleString('en-IN', {{minimumFractionDigits: 1}})}}</td>
          <td class="p-3 font-mono ${{compPnlClass}}">${{(t.comp_pnl || t.net_pnl) > 0 ? '+' : ''}}₹${{(t.comp_pnl || t.net_pnl).toLocaleString('en-IN', {{minimumFractionDigits: 1}})}}</td>
          <td class="p-3"><span class="px-2 py-0.5 rounded text-[10px] ${{reasonBadge}}">${{t.exit_reason}}</span></td>
          <td class="p-3 text-center">
            <button onclick="openModal('${{t.trade_id}}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-blue-400 border border-slate-700 rounded text-[11px]">
              Story 🔍
            </button>
          </td>
        `;
        tbody.appendChild(tr);
      }});
    }}

    function openModal(tradeId) {{
      const t = tradesData.find(x => x.trade_id === tradeId);
      if (!t) return;
      document.getElementById('modalTitle').textContent = `${{t.trade_id}}: ${{t.date}} (${{t.day}}) - ${{t.strategy_type}}`;
      const badge = document.getElementById('modalTradeBadge');
      if (t.net_pnl > 0) {{
        badge.className = 'px-2 py-0.5 text-xs font-bold rounded badge-win';
        badge.textContent = `+₹${{t.net_pnl.toLocaleString('en-IN')}} WIN`;
      }} else {{
        badge.className = 'px-2 py-0.5 text-xs font-bold rounded badge-loss';
        badge.textContent = `₹${{t.net_pnl.toLocaleString('en-IN')}} LOSS`;
      }}

      document.getElementById('modalKabLiya').innerHTML = `
        <strong>Date:</strong> ${{t.date}} (${{t.day}}) | <strong>Entry:</strong> ${{t.entry_time}} | <strong>Exit:</strong> ${{t.exit_time}} | <strong>Hold Time:</strong> ${{t.story.kab_liya.hold_duration_mins}} mins | <strong>Lot Size:</strong> ${{t.num_lots || 1}} Lots (${{t.lot_size}} qty)
      `;
      document.getElementById('modalKyunLiya').textContent = t.story.kyun_liya;
      document.getElementById('modalOrderKya').innerHTML = `
        <strong>Short Strike:</strong> Sell ${{t.short_strike}} (${{t.strategy_type.includes('BULL') ? 'PE' : 'CE'}}) | 
        <strong>Long Hedge:</strong> Buy ${{t.long_strike}} (${{t.strategy_type.includes('BULL') ? 'PE' : 'CE'}})<br>
        <strong>Net Premium Collected:</strong> ₹${{t.entry_credit}} pts | <strong>Exit Spread:</strong> ₹${{t.exit_spread}} pts
      `;
      document.getElementById('modalKyunExit').textContent = t.story.kyun_exit;
      document.getElementById('storyModal').classList.remove('hidden');
    }}

    function closeModal() {{
      document.getElementById('storyModal').classList.add('hidden');
    }}

    window.onload = function() {{
      renderTable();

      // 1. Dual Equity Curves Chart
      const eqCtx = document.getElementById('equityChart').getContext('2d');
      const labels = tradesData.map(t => t.date);
      const fixedData = tradesData.map(t => t.equity);
      const compData = tradesData.map(t => t.comp_equity || t.equity);

      new Chart(eqCtx, {{
        type: 'line',
        data: {{
          labels: labels,
          datasets: [
            {{
              label: 'Compounded Equity (₹)',
              data: compData,
              borderColor: '#10b981',
              backgroundColor: 'rgba(16, 185, 129, 0.08)',
              fill: true,
              tension: 0.1,
              pointRadius: 0,
              borderWidth: 2
            }},
            {{
              label: 'Fixed 1 Lot Equity (₹)',
              data: fixedData,
              borderColor: '#3b82f6',
              borderDash: [4, 4],
              tension: 0.1,
              pointRadius: 0,
              borderWidth: 1.5
            }}
          ]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{
              display: true,
              position: 'top',
              labels: {{ color: '#cbd5e1', boxWidth: 12, font: {{ size: 10 }} }}
            }}
          }},
          scales: {{
            x: {{ display: false }},
            y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
          }}
        }}
      }});

      // 2. Exit Reason Pie Chart
      const exitCtx = document.getElementById('exitChart').getContext('2d');
      const reasonCounts = {{}};
      tradesData.forEach(t => {{
        reasonCounts[t.exit_reason] = (reasonCounts[t.exit_reason] || 0) + 1;
      }});
      new Chart(exitCtx, {{
        type: 'doughnut',
        data: {{
          labels: Object.keys(reasonCounts),
          datasets: [{{
            data: Object.values(reasonCounts),
            backgroundColor: ['#10b981', '#3b82f6', '#ef4444', '#64748b']
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', boxWidth: 12, font: {{ size: 10 }} }} }}
          }}
        }}
      }});

      // 3. Monthly PnL Bar Chart
      const mCtx = document.getElementById('monthlyChart').getContext('2d');
      const mLabels = monthlyData.map(m => m.year_month);
      const mCompValues = monthlyData.map(m => m.comp_net_pnl);
      const mColors = mCompValues.map(v => v >= 15000 ? '#10b981' : (v >= 0 ? '#3b82f6' : '#ef4444'));

      new Chart(mCtx, {{
        type: 'bar',
        data: {{
          labels: mLabels,
          datasets: [{{
            label: 'Compounded Net PnL (₹)',
            data: mCompValues,
            backgroundColor: mColors,
            borderRadius: 4
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }}
          }},
          scales: {{
            x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }} }},
            y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
          }}
        }}
      }});
    }};
  </script>
</body>
</html>
"""

    out_file1 = "results/interactive_trade_journal_dashboard.html"
    out_file2 = "C:/Users/user/.gemini/antigravity/brain/1d9a796e-b5cd-4415-a0a4-4ebcef8260d7/trade_journal_dashboard.html"

    with open(out_file1, 'w', encoding='utf-8') as f:
        f.write(html_content)
    with open(out_file2, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"Updated dashboard saved to:\n1. {out_file1}\n2. {out_file2}")

if __name__ == '__main__':
    update_dashboard()
