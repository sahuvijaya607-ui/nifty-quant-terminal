import json
import os

with open('results/monthly_equity_curve_all_strategies.json', 'r', encoding='utf-8') as f:
    points = json.load(f)

json_str = json.dumps(points)

html_code = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>6-Year Master Equity Curves (2020-2026)</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      margin: 0;
      padding: 16px;
      background: #0b0f19;
      color: #e2e8f0;
    }}
    .glow-champion {{
      box-shadow: 0 0 25px rgba(16, 185, 129, 0.25);
      border: 1px solid rgba(16, 185, 129, 0.5);
    }}
    .chart-container {{
      position: relative;
      width: 100%;
      height: 480px;
    }}
    canvas {{
      width: 100%;
      height: 100%;
      display: block;
    }}
    .toggle-btn.active {{
      opacity: 1.0;
      font-weight: 600;
    }}
    .toggle-btn.inactive {{
      opacity: 0.35;
      text-decoration: line-through;
    }}
  </style>
</head>
<body class="bg-[#0b0f19] text-slate-100 antialiased p-4 sm:p-6">

  <!-- Header Banner -->
  <div class="max-w-6xl mx-auto mb-6">
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-800">
      <div>
        <div class="flex items-center gap-3">
          <span class="px-2.5 py-1 text-xs font-bold rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 tracking-wider uppercase">
            6-Year Verified Tick Data
          </span>
          <span class="text-xs text-slate-400">2020 - 2026 (75 Months, 967 Trades)</span>
        </div>
        <h1 class="text-2xl sm:text-3xl font-extrabold text-white mt-1.5 flex items-center gap-2">
          Nifty Quant Desk - Master Equity Curves
        </h1>
        <p class="text-sm text-slate-400 mt-0.5">
          Cumulative Net PnL progression starting with Rs 1,00,000 capital & all real Kotak STT/taxes/charges deducted.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <span class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-950 text-emerald-300 border border-emerald-700/60 flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          Vikalp 2 Live On Render
        </span>
      </div>
    </div>
  </div>

  <!-- Metric Cards Grid -->
  <div class="max-w-6xl mx-auto grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5 mb-6">
    <!-- Vikalp 2 (Champion) -->
    <div class="glow-champion bg-slate-900/90 rounded-xl p-4 transition-all relative overflow-hidden">
      <div class="absolute -right-3 -top-3 w-16 h-16 bg-emerald-500/10 rounded-full blur-xl"></div>
      <div class="flex items-center justify-between">
        <span class="text-[11px] font-bold text-emerald-400 uppercase tracking-wider">Deployed #1</span>
        <span class="text-xs font-black px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300">CHAMPION</span>
      </div>
      <div class="text-sm font-bold text-white mt-1">Vikalp 2 (Rs 2.8k Cap)</div>
      <div class="text-2xl font-black text-emerald-400 mt-2">+Rs 2,47,675</div>
      <div class="mt-2 pt-2 border-t border-slate-800 text-[11px] space-y-0.5 text-slate-400">
        <div class="flex justify-between"><span>Win Rate:</span><span class="font-semibold text-slate-200">57.4% (555W)</span></div>
        <div class="flex justify-between"><span>Max DD:</span><span class="font-semibold text-emerald-300">-Rs 17,981 (Lowest)</span></div>
        <div class="flex justify-between"><span>Hard SL:</span><span class="font-semibold text-slate-200">-Rs 2,800 Lock</span></div>
      </div>
    </div>

    <!-- Vikalp 1 / Model 4 -->
    <div class="bg-slate-900/80 border border-sky-500/40 rounded-xl p-4 transition-all">
      <div class="flex items-center justify-between">
        <span class="text-[11px] font-bold text-sky-400 uppercase tracking-wider">Model 4</span>
        <span class="text-xs font-bold text-slate-400">Base Champ</span>
      </div>
      <div class="text-sm font-bold text-white mt-1">Vikalp 1 (60% SL)</div>
      <div class="text-2xl font-black text-sky-400 mt-2">+Rs 2,63,424</div>
      <div class="mt-2 pt-2 border-t border-slate-800 text-[11px] space-y-0.5 text-slate-400">
        <div class="flex justify-between"><span>Win Rate:</span><span class="font-semibold text-slate-200">58.8%</span></div>
        <div class="flex justify-between"><span>Max DD:</span><span class="font-semibold text-rose-400">-Rs 26,270</span></div>
        <div class="flex justify-between"><span>Drawdown:</span><span class="font-semibold text-rose-400">+46% higher risk</span></div>
      </div>
    </div>

    <!-- Model 1 (3-Day ATM) -->
    <div class="bg-slate-900/80 border border-purple-500/40 rounded-xl p-4 transition-all">
      <div class="flex items-center justify-between">
        <span class="text-[11px] font-bold text-purple-400 uppercase tracking-wider">Model 1</span>
        <span class="text-xs font-bold text-slate-400">3-Day ATM</span>
      </div>
      <div class="text-sm font-bold text-white mt-1">Tue-Thu-Fri Trend</div>
      <div class="text-2xl font-black text-purple-400 mt-2">+Rs 2,16,305</div>
      <div class="mt-2 pt-2 border-t border-slate-800 text-[11px] space-y-0.5 text-slate-400">
        <div class="flex justify-between"><span>Win Rate:</span><span class="font-semibold text-slate-200">59.2%</span></div>
        <div class="flex justify-between"><span>Max DD:</span><span class="font-semibold text-rose-400">-Rs 26,312</span></div>
        <div class="flex justify-between"><span>Trades:</span><span class="font-semibold text-slate-200">654</span></div>
      </div>
    </div>

    <!-- Model 2 (Balanced 45D) -->
    <div class="bg-slate-900/80 border border-amber-500/40 rounded-xl p-4 transition-all">
      <div class="flex items-center justify-between">
        <span class="text-[11px] font-bold text-amber-400 uppercase tracking-wider">Model 2</span>
        <span class="text-xs font-bold text-slate-400">45-Delta</span>
      </div>
      <div class="text-sm font-bold text-white mt-1">Balanced OTM</div>
      <div class="text-2xl font-black text-amber-400 mt-2">+Rs 1,44,263</div>
      <div class="mt-2 pt-2 border-t border-slate-800 text-[11px] space-y-0.5 text-slate-400">
        <div class="flex justify-between"><span>Win Rate:</span><span class="font-semibold text-slate-200">57.8%</span></div>
        <div class="flex justify-between"><span>Max DD:</span><span class="font-semibold text-rose-400">-Rs 25,760</span></div>
        <div class="flex justify-between"><span>Trades:</span><span class="font-semibold text-slate-200">658</span></div>
      </div>
    </div>

    <!-- Model 3 (Fixed 900) -->
    <div class="bg-slate-900/80 border border-rose-500/40 rounded-xl p-4 transition-all">
      <div class="flex items-center justify-between">
        <span class="text-[11px] font-bold text-rose-400 uppercase tracking-wider">Model 3</span>
        <span class="text-xs font-bold text-slate-400">Scalper</span>
      </div>
      <div class="text-sm font-bold text-white mt-1">Rs 900 Target</div>
      <div class="text-2xl font-black text-rose-400 mt-2">+Rs 48,182</div>
      <div class="mt-2 pt-2 border-t border-slate-800 text-[11px] space-y-0.5 text-slate-400">
        <div class="flex justify-between"><span>Win Rate:</span><span class="font-semibold text-emerald-400">71.0% (Peak)</span></div>
        <div class="flex justify-between"><span>Max DD:</span><span class="font-semibold text-rose-400">-Rs 26,828</span></div>
        <div class="flex justify-between"><span>Avg/Trade:</span><span class="font-semibold text-slate-200">Rs 56.8</span></div>
      </div>
    </div>
  </div>

  <!-- Interactive Controls & Toggles -->
  <div class="max-w-6xl mx-auto bg-slate-900 border border-slate-800 rounded-2xl p-5 mb-6 shadow-xl">
    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
      <h3 class="text-base font-bold text-white flex items-center gap-2">
        📈 6-Year Cumulative Net Equity Growth Comparison (Rs)
      </h3>
      <div class="flex flex-wrap items-center gap-2" id="legendContainer">
        <!-- Toggles dynamically injected -->
      </div>
    </div>

    <!-- Chart Canvas -->
    <div class="chart-container">
      <canvas id="equityChart"></canvas>
      <div id="tooltip" class="absolute hidden pointer-events-none bg-slate-950/95 border border-slate-700 rounded-lg p-3 text-xs shadow-2xl z-20 min-w-[210px]"></div>
    </div>

    <div class="mt-3 pt-3 border-t border-slate-800/80 flex flex-col sm:flex-row sm:items-center justify-between text-xs text-slate-400 gap-2">
      <span>💡 <b>Pro Insight:</b> Click any strategy button above to toggle it on/off. Vikalp 2 (Emerald Green) delivers the smoothest line with the smallest drawdown.</span>
      <span class="text-slate-500">75 Monthly Points | Jan 2020 - Mar 2026</span>
    </div>
  </div>

  <script>
    const rawData = {json_str};

    const strategies = [
      {{ id: 'Vikalp_2', name: 'Vikalp 2 (Hard SL Rs 2.8k Cap)', color: '#10b981', active: true, width: 3.5 }},
      {{ id: 'Vikalp_1', name: 'Vikalp 1 (Base Model 4)', color: '#38bdf8', active: true, width: 2 }},
      {{ id: 'Model_1', name: 'Model 1 (3-Day ATM)', color: '#a855f7', active: true, width: 2 }},
      {{ id: 'Model_2', name: 'Model 2 (45-Delta)', color: '#f59e0b', active: true, width: 2 }},
      {{ id: 'Model_3', name: 'Model 3 (Rs 900 Target)', color: '#f43f5e', active: false, width: 1.5 }}
    ];

    const canvas = document.getElementById('equityChart');
    const ctx = canvas.getContext('2d');
    const tooltip = document.getElementById('tooltip');
    const legendContainer = document.getElementById('legendContainer');

    function renderLegend() {{
      legendContainer.innerHTML = '';
      strategies.forEach(s => {{
        const btn = document.createElement('button');
        btn.className = `toggle-btn px-2.5 py-1 text-xs rounded-full border transition-all flex items-center gap-1.5 ${{s.active ? 'active' : 'inactive'}}`;
        btn.style.borderColor = s.color;
        btn.style.color = s.color;
        btn.style.backgroundColor = s.active ? `${{s.color}}18` : 'transparent';
        btn.innerHTML = `<span class="w-2 h-2 rounded-full" style="background:${{s.color}}"></span> ${{s.name}}`;
        btn.onclick = () => {{
          s.active = !s.active;
          renderLegend();
          drawChart();
        }};
        legendContainer.appendChild(btn);
      }});
    }}

    function resizeCanvas() {{
      const rect = canvas.parentElement.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      drawChart();
    }}

    let mouseX = -1;

    canvas.parentElement.addEventListener('mousemove', (e) => {{
      const rect = canvas.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      drawChart();
      updateTooltip(e.clientX, e.clientY, rect);
    }});

    canvas.parentElement.addEventListener('mouseleave', () => {{
      mouseX = -1;
      tooltip.classList.add('hidden');
      drawChart();
    }});

    function updateTooltip(clientX, clientY, rect) {{
      const width = rect.width;
      const height = rect.height;
      const padLeft = 70;
      const padRight = 30;
      const chartW = width - padLeft - padRight;

      if (mouseX < padLeft || mouseX > width - padRight) {{
        tooltip.classList.add('hidden');
        return;
      }}

      const ratio = (mouseX - padLeft) / chartW;
      const idx = Math.min(rawData.length - 1, Math.max(0, Math.round(ratio * (rawData.length - 1))));
      const pt = rawData[idx];

      let html = `<div class="font-bold text-white border-b border-slate-800 pb-1.5 mb-1.5 flex justify-between">
                    <span>Month: ${{pt.ym}}</span>
                    <span class="text-slate-400">Pt #${{idx + 1}}/75</span>
                  </div>`;

      strategies.forEach(s => {{
        if (s.active) {{
          const val = pt[s.id] || 0;
          html += `<div class="flex items-center justify-between gap-4 py-0.5">
                     <span class="flex items-center gap-1.5" style="color: ${{s.color}}">
                       <span class="w-2 h-2 rounded-full" style="background:${{s.color}}"></span>
                       ${{s.id.replace('_', ' ')}}:
                     </span>
                     <span class="font-bold text-white">Rs ${{val.toLocaleString('en-IN')}}</span>
                   </div>`;
        }}
      }});

      tooltip.innerHTML = html;
      tooltip.classList.remove('hidden');

      const tooltipW = 220;
      let left = mouseX + 15;
      if (left + tooltipW > width) left = mouseX - tooltipW - 15;
      let top = 40;

      tooltip.style.left = `${{left}}px`;
      tooltip.style.top = `${{top}}px`;
    }}

    function drawChart() {{
      const rect = canvas.parentElement.getBoundingClientRect();
      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);

      const padTop = 30;
      const padBottom = 45;
      const padLeft = 70;
      const padRight = 30;
      const chartW = w - padLeft - padRight;
      const chartH = h - padTop - padBottom;

      let maxY = 300000;
      let minY = -10000;

      // Draw Grid Lines & Y Axis
      ctx.strokeStyle = '#1e293b';
      ctx.lineWidth = 1;
      ctx.fillStyle = '#64748b';
      ctx.font = '11px -apple-system, sans-serif';
      ctx.textAlign = 'right';

      const ySteps = 6;
      for (let i = 0; i <= ySteps; i++) {{
        const yVal = minY + (i / ySteps) * (maxY - minY);
        const yPos = padTop + chartH - (i / ySteps) * chartH;
        
        ctx.beginPath();
        ctx.moveTo(padLeft, yPos);
        ctx.lineTo(w - padRight, yPos);
        ctx.stroke();

        ctx.fillText('Rs ' + Math.round(yVal / 1000) + 'k', padLeft - 10, yPos + 4);
      }}

      // Draw Zero line stronger
      const zeroY = padTop + chartH - ((0 - minY) / (maxY - minY)) * chartH;
      ctx.strokeStyle = '#334155';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(padLeft, zeroY);
      ctx.lineTo(w - padRight, zeroY);
      ctx.stroke();

      // Draw X Axis Labels (Yearly)
      ctx.textAlign = 'center';
      rawData.forEach((pt, i) => {{
        if (pt.ym.endsWith('-01')) {{
          const xPos = padLeft + (i / (rawData.length - 1)) * chartW;
          ctx.strokeStyle = '#1e293b';
          ctx.beginPath();
          ctx.moveTo(xPos, padTop);
          ctx.lineTo(xPos, padTop + chartH);
          ctx.stroke();

          ctx.fillStyle = '#94a3b8';
          ctx.fillText(pt.ym.split('-')[0], xPos, h - 18);
        }}
      }});

      // Draw Strategy Curves
      strategies.forEach(s => {{
        if (!s.active) return;

        ctx.strokeStyle = s.color;
        ctx.lineWidth = s.width;
        ctx.beginPath();

        rawData.forEach((pt, i) => {{
          const val = pt[s.id] || 0;
          const x = padLeft + (i / (rawData.length - 1)) * chartW;
          const y = padTop + chartH - ((val - minY) / (maxY - minY)) * chartH;

          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }});
        ctx.stroke();

        // Area gradient fill for Vikalp 2
        if (s.id === 'Vikalp_2') {{
          const grad = ctx.createLinearGradient(0, padTop, 0, padTop + chartH);
          grad.addColorStop(0, 'rgba(16, 185, 129, 0.25)');
          grad.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

          ctx.fillStyle = grad;
          ctx.beginPath();
          rawData.forEach((pt, i) => {{
            const val = pt[s.id] || 0;
            const x = padLeft + (i / (rawData.length - 1)) * chartW;
            const y = padTop + chartH - ((val - minY) / (maxY - minY)) * chartH;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }});
          ctx.lineTo(padLeft + chartW, zeroY);
          ctx.lineTo(padLeft, zeroY);
          ctx.closePath();
          ctx.fill();
        }}
      }});

      // Draw Crosshair on Hover
      if (mouseX >= padLeft && mouseX <= w - padRight) {{
        ctx.strokeStyle = '#94a3b8';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(mouseX, padTop);
        ctx.lineTo(mouseX, padTop + chartH);
        ctx.stroke();
        ctx.setLineDash([]);

        const ratio = (mouseX - padLeft) / chartW;
        const idx = Math.min(rawData.length - 1, Math.max(0, Math.round(ratio * (rawData.length - 1))));
        const pt = rawData[idx];

        strategies.forEach(s => {{
          if (!s.active) return;
          const val = pt[s.id] || 0;
          const x = padLeft + (idx / (rawData.length - 1)) * chartW;
          const y = padTop + chartH - ((val - minY) / (maxY - minY)) * chartH;

          ctx.fillStyle = s.color;
          ctx.beginPath();
          ctx.arc(x, y, 4.5, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }});
      }}
    }}

    renderLegend();
    window.addEventListener('resize', resizeCanvas);
    setTimeout(resizeCanvas, 50);
  </script>
</body>
</html>
"""

target_file = r'C:\Users\user\.gemini\antigravity\brain\1d9a796e-b5cd-4415-a0a4-4ebcef8260d7\all_strategies_equity_curve.html'
with open(target_file, 'w', encoding='utf-8') as f:
    f.write(html_code)

print("SUCCESS: Generated interactive HTML equity curve at:", target_file)
