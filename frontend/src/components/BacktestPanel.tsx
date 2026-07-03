import { useCallback, useEffect, useRef, useState } from 'react'
import { BarChart3, ChevronDown, ChevronRight, RefreshCw, Search, TrendingUp } from 'lucide-react'
import {
  recommendationsApi,
  stocksApi,
  type AccuracyTrendResponse,
  type ConfidenceCalibrationResponse,
  type StockSignalHistoryResponse,
  type StockSignalHistoryItem,
  type StrategyStatsResponse,
  type StockItem,
} from '@panwatch/api'
import { factorsApi, type FactorIcResponse, type FactorIcEntry } from '@panwatch/api'

// ---------- 常量 ----------
const MARKET_LABELS: Record<string, string> = { CN: 'A股', HK: '港股', US: '美股', ALL: '全部' }
const HORIZONS = [1, 3, 5, 10]
const MARKETS = ['', 'CN', 'HK', 'US']

// ---------- 工具 ----------
function fmt(v: number | null | undefined, d = 1): string {
  if (v == null || Number.isNaN(v)) return '--'
  return Number(v).toFixed(d)
}

function winColor(wr: number | null | undefined): string {
  if (wr == null) return 'text-muted-foreground'
  if (wr >= 60) return 'text-rose-500'
  if (wr >= 50) return 'text-amber-500'
  return 'text-emerald-600'
}

function winBg(wr: number | null | undefined): string {
  if (wr == null) return 'bg-muted/30'
  if (wr >= 65) return 'bg-rose-500/20 border-rose-500/30'
  if (wr >= 55) return 'bg-amber-500/10 border-amber-500/20'
  if (wr >= 45) return 'bg-muted/30 border-border/40'
  return 'bg-emerald-500/10 border-emerald-500/20'
}

// ---------- 策略 × 持有期热力图 ----------
function StrategyHeatmap({ stats }: { stats: StrategyStatsResponse | null }) {
  const rows = stats?.by_strategy || []
  if (!rows.length) return <div className="text-[12px] text-muted-foreground text-center py-6">暂无回测数据</div>

  // pivot: strategyCode → { market, horizonDays → { win_rate, sample_size } }
  type Cell = { win_rate: number; sample_size: number; avg_return_pct: number }
  const keys: Set<string> = new Set()
  const map: Record<string, Record<number, Cell>> = {}
  const nameMap: Record<string, string> = {}

  for (const r of rows) {
    const key = `${r.strategy_code}||${r.market}`
    keys.add(key)
    nameMap[key] = `${r.strategy_name || r.strategy_code} (${MARKET_LABELS[r.market] || r.market})`
    if (!map[key]) map[key] = {}
    map[key][r.horizon_days] = { win_rate: r.win_rate, sample_size: r.sample_size, avg_return_pct: r.avg_return_pct }
  }

  const sortedKeys = Array.from(keys).sort()

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b border-border/40">
            <th className="text-left py-2 px-2 text-muted-foreground font-medium min-w-[140px]">策略 · 市场</th>
            {HORIZONS.map(h => (
              <th key={h} className="text-center py-2 px-2 text-muted-foreground font-medium">{h}日胜率</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedKeys.map(key => (
            <tr key={key} className="border-b border-border/20 hover:bg-accent/10">
              <td className="py-2 px-2 text-[11px] text-foreground">{nameMap[key]}</td>
              {HORIZONS.map(h => {
                const cell = map[key]?.[h]
                return (
                  <td key={h} className="text-center py-1 px-1">
                    {cell ? (
                      <div className={`rounded px-1.5 py-0.5 border text-center ${winBg(cell.win_rate)}`}>
                        <div className={`font-mono font-bold ${winColor(cell.win_rate)}`}>{fmt(cell.win_rate)}%</div>
                        <div className="text-[9px] text-muted-foreground mt-0.5">n={cell.sample_size} · {cell.avg_return_pct >= 0 ? '+' : ''}{fmt(cell.avg_return_pct, 2)}%</div>
                      </div>
                    ) : (
                      <span className="text-muted-foreground/40">—</span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------- 周级胜率趋势折线图（SVG） ----------
function WinRateTrend({ data }: { data: AccuracyTrendResponse | null }) {
  if (!data || !data.periods.length) return (
    <div className="text-[12px] text-muted-foreground text-center py-6">暂无趋势数据</div>
  )
  const pts = data.periods
  const W = 460, H = 100, PADL = 30, PADR = 10, PADT = 8, PADB = 20

  const wr = pts.map(p => p.win_rate)
  const minW = Math.min(...wr, 30)
  const maxW = Math.max(...wr, 70)
  const range = maxW - minW || 1

  const chartW = W - PADL - PADR
  const chartH = H - PADT - PADB

  const px = (i: number) => PADL + (i / (pts.length - 1 || 1)) * chartW
  const py = (v: number) => PADT + (1 - (v - minW) / range) * chartH

  const polyline = pts.map((p, i) => `${px(i)},${py(p.win_rate)}`).join(' ')

  // 50% 参考线
  const refY = py(50)
  const showRef = 50 >= minW && 50 <= maxW

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 280 }}>
        {showRef && (
          <>
            <line x1={PADL} y1={refY} x2={W - PADR} y2={refY} stroke="currentColor" strokeOpacity={0.2} strokeDasharray="3 3" className="text-muted-foreground" />
            <text x={PADL - 2} y={refY + 3} fontSize={8} textAnchor="end" fill="currentColor" opacity={0.4}>50%</text>
          </>
        )}
        <polyline points={polyline} fill="none" stroke="hsl(var(--primary))" strokeWidth={1.5} strokeLinejoin="round" />
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={px(i)} cy={py(p.win_rate)} r={2.5} fill="hsl(var(--primary))" />
            <title>{p.period}: {fmt(p.win_rate)}% (n={p.total})</title>
          </g>
        ))}
        {/* X轴标签：首尾 + 中间最多3个 */}
        {[0, Math.floor(pts.length / 2), pts.length - 1].filter((v, i, a) => a.indexOf(v) === i && pts[v]).map(i => (
          <text key={i} x={px(i)} y={H - 4} fontSize={8} textAnchor="middle" fill="currentColor" opacity={0.5}>{pts[i].period.replace(/^\d{4}-/, '')}</text>
        ))}
        {/* Y轴左侧标签 */}
        {[minW, maxW].map(v => (
          <text key={v} x={PADL - 2} y={py(v) + 3} fontSize={8} textAnchor="end" fill="currentColor" opacity={0.4}>{fmt(v)}%</text>
        ))}
      </svg>
    </div>
  )
}

// ---------- 置信度校准图 ----------
function CalibrationChart({ data }: { data: ConfidenceCalibrationResponse | null }) {
  if (!data || !data.buckets.length) return (
    <div className="text-[12px] text-muted-foreground text-center py-6">暂无校准数据</div>
  )
  const buckets = data.buckets.filter(b => b.total > 0)
  if (!buckets.length) return <div className="text-[12px] text-muted-foreground text-center py-6">样本不足（需有 confidence 字段的信号）</div>

  const maxWr = Math.max(...buckets.map(b => b.win_rate ?? 0), 100)

  return (
    <div className="space-y-2">
      {buckets.map(b => (
        <div key={b.bucket} className="flex items-center gap-2">
          <div className="w-[72px] text-[10px] text-muted-foreground shrink-0 text-right">{b.bucket}</div>
          <div className="flex-1 relative h-5 bg-muted/30 rounded overflow-hidden">
            <div
              className="h-full rounded transition-all"
              style={{
                width: `${((b.win_rate ?? 0) / maxWr) * 100}%`,
                background: (b.win_rate ?? 0) >= 60 ? 'hsl(var(--primary)/0.6)' : (b.win_rate ?? 0) >= 50 ? 'hsl(40 90% 55% / 0.5)' : 'hsl(150 60% 45% / 0.4)',
              }}
            />
            <span className="absolute inset-0 flex items-center justify-start px-1.5 text-[10px] font-mono font-medium">
              {b.win_rate != null ? `${fmt(b.win_rate)}%` : '--'}
            </span>
          </div>
          <div className="w-12 text-[10px] text-muted-foreground shrink-0">n={b.total}</div>
        </div>
      ))}
      <div className="text-[10px] text-muted-foreground pt-1">
        理想情况：置信度越高，实际胜率越高。若高置信度桶胜率反而低，说明模型过度自信。
      </div>
    </div>
  )
}

const FACTOR_LABELS: Record<string, string> = {
  alpha_score: '选股 α',
  catalyst_score: '事件催化',
  quality_score: '计划质量',
  risk_penalty: '风险惩罚',
  crowd_penalty: '拥挤惩罚',
  final_score: '综合评分',
}

// ---------- 因子 IC 表 ----------
function FactorIcTable({ data }: { data: FactorIcResponse | null }) {
  if (!data || !data.factors || Object.keys(data.factors).length === 0) return (
    <div className="text-[12px] text-muted-foreground text-center py-6">暂无因子 IC 数据</div>
  )

  const entries = Object.entries(data.factors as Record<string, FactorIcEntry>)
    .sort(([, a], [, b]) => Math.abs(b.ic ?? 0) - Math.abs(a.ic ?? 0))

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b border-border/40">
            <th className="text-left py-1.5 px-2 text-muted-foreground font-medium">因子</th>
            <th className="text-right py-1.5 px-2 text-muted-foreground font-medium">IC</th>
            <th className="text-right py-1.5 px-2 text-muted-foreground font-medium">IR</th>
            <th className="text-right py-1.5 px-2 text-muted-foreground font-medium">样本</th>
            <th className="text-right py-1.5 px-2 text-muted-foreground font-medium">结论</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([code, f]) => {
            const ic = f.ic ?? null
            const ir = f.ir ?? null
            const effective = ic != null && Math.abs(ic) >= 0.02 && (ir == null || Math.abs(ir) >= 0.3)
            return (
              <tr key={code} className="border-b border-border/20 hover:bg-accent/10">
                <td className="py-1.5 px-2 font-medium">{FACTOR_LABELS[code] || code}</td>
                <td className={`text-right py-1.5 px-2 font-mono ${ic != null && Math.abs(ic) >= 0.02 ? 'text-primary font-bold' : 'text-muted-foreground'}`}>
                  {ic != null ? (ic >= 0 ? '+' : '') + fmt(ic, 3) : '--'}
                </td>
                <td className={`text-right py-1.5 px-2 font-mono ${ir != null && Math.abs(ir) >= 0.5 ? 'text-primary' : 'text-muted-foreground'}`}>
                  {ir != null ? (ir >= 0 ? '+' : '') + fmt(ir, 2) : '--'}
                </td>
                <td className="text-right py-1.5 px-2 text-muted-foreground">{f.sample_size ?? '--'}</td>
                <td className="text-right py-1.5 px-2">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${effective ? 'bg-primary/10 text-primary border-primary/20' : 'bg-muted/30 text-muted-foreground border-border/30'}`}>
                    {effective ? '有效' : ic == null ? '无数据' : '弱'}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="text-[10px] text-muted-foreground mt-2">|IC| ≥ 0.02 且 |IR| ≥ 0.3 视为有效因子</div>
    </div>
  )
}

// ---------- 市场胜率对比 ----------
function MarketBreakdown({ stats }: { stats: StrategyStatsResponse | null }) {
  const rows = stats?.by_market || []
  if (!rows.length) return <div className="text-[12px] text-muted-foreground text-center py-6">暂无市场数据</div>
  return (
    <div className="space-y-2">
      {rows.map(r => (
        <div key={r.market} className="flex items-center justify-between text-[12px] py-1 border-b border-border/20 last:border-0">
          <span className="font-medium">{MARKET_LABELS[r.market] || r.market}</span>
          <div className="flex items-center gap-3">
            <span className={`font-mono font-bold ${winColor(r.win_rate)}`}>{fmt(r.win_rate)}%</span>
            <span className={`text-[11px] font-mono ${r.avg_return_pct >= 0 ? 'text-rose-500' : 'text-emerald-600'}`}>
              {r.avg_return_pct >= 0 ? '+' : ''}{fmt(r.avg_return_pct, 2)}%
            </span>
            <span className="text-muted-foreground text-[10px]">n={r.total}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------- 单条信号行 ----------
function SignalRow({ item, horizon }: { item: StockSignalHistoryItem; horizon: number }) {
  const [open, setOpen] = useState(false)
  const o = item.outcome
  const evaluated = o && o.outcome_status !== 'pending' && o.outcome_status !== ''
  const ret = o?.outcome_return_pct ?? null
  const hit_target = o?.hit_target
  const hit_stop = o?.hit_stop

  const actionColor: Record<string, string> = {
    buy: 'bg-rose-500/10 text-rose-600 border-rose-500/20',
    add: 'bg-orange-500/10 text-orange-600 border-orange-500/20',
    watch: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
    sell: 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20',
    reduce: 'bg-teal-500/10 text-teal-600 border-teal-500/20',
  }

  return (
    <div className="border-b border-border/20 last:border-0">
      <div
        className="flex items-center gap-2 py-2.5 px-3 hover:bg-accent/10 cursor-pointer"
        onClick={() => setOpen(v => !v)}
      >
        <span className="text-muted-foreground flex-shrink-0">
          {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </span>
        {/* 日期 */}
        <span className="text-[11px] text-muted-foreground w-20 flex-shrink-0">{item.snapshot_date}</span>
        {/* 操作 */}
        <span className={`text-[10px] px-1.5 py-0.5 rounded border flex-shrink-0 ${actionColor[item.action] || 'bg-muted/30 text-muted-foreground border-border/30'}`}>
          {item.action_label}
        </span>
        {/* 策略 */}
        <span className="text-[11px] text-muted-foreground truncate flex-1 min-w-0">{item.strategy_name}</span>
        {/* 结果 */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {!evaluated ? (
            <span className="text-[10px] text-muted-foreground/50">待评估</span>
          ) : ret !== null ? (
            <>
              <span className={`text-[12px] font-mono font-bold ${ret > 0 ? 'text-rose-500' : ret < 0 ? 'text-emerald-600' : 'text-muted-foreground'}`}>
                {ret > 0 ? '+' : ''}{fmt(ret, 2)}%
              </span>
              {hit_target && <span className="text-[9px] px-1 py-0.5 rounded bg-rose-500/10 text-rose-600 border border-rose-500/20">达标</span>}
              {hit_stop && <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">止损</span>}
            </>
          ) : (
            <span className="text-[10px] text-muted-foreground/50">—</span>
          )}
        </div>
      </div>
      {open && (
        <div className="bg-accent/10 px-6 py-3 text-[11px] space-y-1.5 border-t border-border/20">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {item.entry_low != null && (
              <div><span className="text-muted-foreground">建议入场</span><br /><span className="font-mono">{fmt(item.entry_low)} – {fmt(item.entry_high ?? item.entry_low)}</span></div>
            )}
            {item.target_price != null && (
              <div><span className="text-muted-foreground">目标价</span><br /><span className="font-mono text-rose-500">{fmt(item.target_price)}</span></div>
            )}
            {item.stop_loss != null && (
              <div><span className="text-muted-foreground">止损价</span><br /><span className="font-mono text-emerald-600">{fmt(item.stop_loss)}</span></div>
            )}
            {o?.base_price != null && (
              <div><span className="text-muted-foreground">基准价 → {horizon}日后</span><br /><span className="font-mono">{fmt(o.base_price)} → {fmt(o.outcome_price ?? null)}</span></div>
            )}
          </div>
          {item.reason && <p className="text-muted-foreground leading-relaxed mt-1">{item.reason}</p>}
        </div>
      )}
    </div>
  )
}

// ---------- 股票信号历史面板 ----------
function StockBacktestSearch() {
  const [stocks, setStocks] = useState<StockItem[]>([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<StockItem | null>(null)
  const [showDropdown, setShowDropdown] = useState(false)
  const [horizon, setHorizon] = useState(3)
  const [history, setHistory] = useState<StockSignalHistoryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    stocksApi.list().then(setStocks).catch(() => {})
  }, [])

  const filtered = stocks.filter(s =>
    query.trim() === '' ? true :
    s.symbol.toLowerCase().includes(query.toLowerCase()) ||
    s.name.includes(query)
  ).slice(0, 10)

  const loadHistory = useCallback(async (stock: StockItem, h: number) => {
    setLoading(true)
    try {
      const data = await recommendationsApi.getStockSignalHistory({
        symbol: stock.symbol,
        market: stock.market,
        days: 365,
        horizon: h,
      })
      setHistory(data)
    } catch { setHistory(null) } finally { setLoading(false) }
  }, [])

  const pick = (s: StockItem) => {
    setSelected(s)
    setQuery(`${s.symbol} ${s.name}`)
    setShowDropdown(false)
    loadHistory(s, horizon)
  }

  const onHorizonChange = (h: number) => {
    setHorizon(h)
    if (selected) loadHistory(selected, h)
  }

  return (
    <div className="space-y-3">
      {/* 搜索栏 */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => { setQuery(e.target.value); setShowDropdown(true) }}
            onFocus={() => setShowDropdown(true)}
            onBlur={() => setTimeout(() => setShowDropdown(false), 150)}
            placeholder="搜索自选股（代码或名称）"
            className="w-full h-8 pl-8 pr-3 text-[12px] rounded border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          {showDropdown && filtered.length > 0 && (
            <div className="absolute top-9 left-0 right-0 bg-popover border border-border rounded shadow-md z-50 max-h-48 overflow-y-auto">
              {filtered.map(s => (
                <button
                  key={`${s.market}:${s.symbol}`}
                  onMouseDown={() => pick(s)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-accent text-left"
                >
                  <span className="font-mono font-bold">{s.symbol}</span>
                  <span className="text-muted-foreground">{s.name}</span>
                  <span className="ml-auto text-[10px] text-muted-foreground">{s.market}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <select
          value={horizon}
          onChange={e => onHorizonChange(Number(e.target.value))}
          className="h-8 px-2 text-[11px] rounded border border-border bg-background text-foreground"
        >
          {HORIZONS.map(h => <option key={h} value={h}>{h}日后涨跌</option>)}
        </select>
        {selected && (
          <button onClick={() => loadHistory(selected, horizon)} disabled={loading}
            className="h-8 px-2 flex items-center gap-1 text-[11px] rounded border border-border hover:bg-accent transition-colors disabled:opacity-50">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          </button>
        )}
      </div>

      {/* 汇总统计 */}
      {history && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            <div className="rounded-lg bg-accent/30 p-2.5 text-center">
              <div className="text-[10px] text-muted-foreground">共推荐</div>
              <div className="text-[18px] font-bold mt-0.5">{history.total_signals}</div>
              <div className="text-[10px] text-muted-foreground">次</div>
            </div>
            <div className="rounded-lg bg-accent/30 p-2.5 text-center">
              <div className="text-[10px] text-muted-foreground">已评估</div>
              <div className="text-[18px] font-bold mt-0.5">{history.evaluated_count}</div>
              <div className="text-[10px] text-muted-foreground">条</div>
            </div>
            <div className="rounded-lg bg-accent/30 p-2.5 text-center">
              <div className="text-[10px] text-muted-foreground">胜率</div>
              <div className={`text-[18px] font-bold mt-0.5 ${winColor(history.win_rate)}`}>
                {history.win_rate != null ? `${history.win_rate}%` : '--'}
              </div>
              <div className="text-[10px] text-muted-foreground">{history.win_count} 胜</div>
            </div>
            <div className="rounded-lg bg-accent/30 p-2.5 text-center">
              <div className="text-[10px] text-muted-foreground">平均{history.horizon_days}日涨跌</div>
              <div className={`text-[18px] font-bold mt-0.5 font-mono ${(history.avg_return_pct ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-600'}`}>
                {history.avg_return_pct != null ? `${history.avg_return_pct >= 0 ? '+' : ''}${fmt(history.avg_return_pct)}%` : '--'}
              </div>
            </div>
            <div className="rounded-lg bg-accent/30 p-2.5 text-center">
              <div className="text-[10px] text-muted-foreground">股票</div>
              <div className="text-[14px] font-bold mt-0.5 font-mono">{history.symbol}</div>
              <div className="text-[10px] text-muted-foreground truncate">{history.stock_name}</div>
            </div>
          </div>

          {/* 信号时间线 */}
          {history.items.length === 0 ? (
            <div className="text-[12px] text-muted-foreground text-center py-6">暂无该股票的历史信号记录</div>
          ) : (
            <div className="card overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 border-b border-border/40 bg-accent/20">
                <span className="text-[11px] font-medium text-foreground">
                  信号历史（最近 {history.items.length} 条）
                </span>
                <span className="text-[10px] text-muted-foreground">点击展开查看入场区间与理由</span>
              </div>
              <div>
                {history.items.map(item => (
                  <SignalRow key={item.signal_run_id} item={item} horizon={history.horizon_days} />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {!selected && (
        <div className="text-[12px] text-muted-foreground text-center py-6">
          搜索一只自选股，查看系统历史上给过什么信号、{horizon}天后实际涨了还是跌了
        </div>
      )}
    </div>
  )
}

// ---------- 主组件 ----------
interface Props {
  stats: StrategyStatsResponse | null
}

export default function BacktestPanel({ stats }: Props) {
  const [horizon, setHorizon] = useState(3)
  const [market, setMarket] = useState('')
  const [granularity, setGranularity] = useState<'week' | 'month'>('week')
  const [trend, setTrend] = useState<AccuracyTrendResponse | null>(null)
  const [calibration, setCalibration] = useState<ConfidenceCalibrationResponse | null>(null)
  const [factorIc, setFactorIc] = useState<FactorIcResponse | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [t, c, f] = await Promise.allSettled([
        recommendationsApi.getAccuracyTrend({ days: 180, horizon, market, granularity }),
        recommendationsApi.getConfidenceCalibration({ days: 180, horizon, market }),
        factorsApi.getFactorIc({ days: 90, horizon }),
      ])
      if (t.status === 'fulfilled') setTrend(t.value)
      if (c.status === 'fulfilled') setCalibration(c.value)
      if (f.status === 'fulfilled') setFactorIc(f.value)
    } finally {
      setLoading(false)
    }
  }, [horizon, market, granularity])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-5">
      {/* 控制栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <BarChart3 className="w-4 h-4 text-primary" />
        <span className="text-[13px] font-semibold text-foreground">历史回测与模型准确性</span>
        <div className="ml-auto flex items-center gap-2">
          <select
            value={horizon}
            onChange={e => setHorizon(Number(e.target.value))}
            className="h-7 px-2 text-[11px] rounded border border-border bg-background text-foreground"
          >
            {HORIZONS.map(h => <option key={h} value={h}>{h}日持有期</option>)}
          </select>
          <select
            value={market}
            onChange={e => setMarket(e.target.value)}
            className="h-7 px-2 text-[11px] rounded border border-border bg-background text-foreground"
          >
            {MARKETS.map(m => <option key={m} value={m}>{m ? (MARKET_LABELS[m] || m) : '全市场'}</option>)}
          </select>
          <select
            value={granularity}
            onChange={e => setGranularity(e.target.value as 'week' | 'month')}
            className="h-7 px-2 text-[11px] rounded border border-border bg-background text-foreground"
          >
            <option value="week">按周</option>
            <option value="month">按月</option>
          </select>
          <button onClick={load} disabled={loading} className="flex items-center gap-1 h-7 px-2 text-[11px] rounded border border-border hover:bg-accent transition-colors disabled:opacity-50">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {/* 0. 按股票查看 */}
      <div className="card p-4">
        <div className="text-[12px] font-semibold text-foreground mb-3 flex items-center gap-1.5">
          <Search className="w-3.5 h-3.5 text-primary" />
          按股票查看历史信号
          <span className="text-[10px] text-muted-foreground font-normal ml-1">（选一只自选股，看系统历史上给了什么建议、后来涨跌如何）</span>
        </div>
        <StockBacktestSearch />
      </div>

      {/* 1. 策略热力图 */}
      <div className="card p-4">
        <div className="text-[12px] font-semibold text-foreground mb-3 flex items-center gap-1.5">
          <TrendingUp className="w-3.5 h-3.5 text-primary" />
          策略 × 持有期胜率热力图
          <span className="text-[10px] text-muted-foreground font-normal ml-1">（按实际后验结果计算）</span>
        </div>
        <StrategyHeatmap stats={stats} />
      </div>

      {/* 2. 胜率趋势 + 市场对比 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="card p-4 md:col-span-2">
          <div className="text-[12px] font-semibold text-foreground mb-3">
            {horizon}日胜率走势
            <span className="text-[10px] text-muted-foreground font-normal ml-2">
              {trend ? `共 ${trend.periods.reduce((s, p) => s + p.total, 0)} 个样本` : ''}
            </span>
          </div>
          <WinRateTrend data={trend} />
        </div>
        <div className="card p-4">
          <div className="text-[12px] font-semibold text-foreground mb-3">市场胜率对比</div>
          <MarketBreakdown stats={stats} />
        </div>
      </div>

      {/* 3. 置信度校准 + 因子 IC */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card p-4">
          <div className="text-[12px] font-semibold text-foreground mb-3">
            置信度校准
            <span className="text-[10px] text-muted-foreground font-normal ml-2">
              {calibration?.total_samples ? `${calibration.total_samples} 个有效样本` : ''}
            </span>
          </div>
          <CalibrationChart data={calibration} />
        </div>
        <div className="card p-4">
          <div className="text-[12px] font-semibold text-foreground mb-3">
            因子 IC / IR
            <span className="text-[10px] text-muted-foreground font-normal ml-2">预测力有效性</span>
          </div>
          <FactorIcTable data={factorIc} />
        </div>
      </div>
    </div>
  )
}
