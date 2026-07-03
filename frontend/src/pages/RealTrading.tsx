import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Trash2, Trophy, BarChart3, ChevronDown, ChevronRight, DollarSign, Pencil, RefreshCw } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

// ---------- 类型 ----------
interface Lot {
  id: number
  stock_symbol: string
  stock_market: string
  stock_name: string
  quantity: number
  buy_price: number
  commission: number
  bought_at: string
  note: string
  status: string
  remaining_qty: number
  sold_qty: number
  realized_pnl: number
  sell_count: number
}

interface Sell {
  id: number
  lot_id: number
  stock_symbol: string
  stock_market: string
  stock_name: string
  quantity: number
  sell_price: number
  sell_currency: string
  commission: number
  sold_at: string
  note: string
  pnl: number
  pnl_pct: number
  holding_days: number
}

interface Summary {
  total_closed_trades: number
  total_pnl: number
  win_count: number
  lose_count: number
  win_rate: number
  avg_pnl_pct: number
  avg_holding_days: number
  best_trade: Sell | null
  worst_trade: Sell | null
  by_stock: Array<{ symbol: string; market: string; name: string; total_pnl: number; trade_count: number; win_rate: number }>
  open_lots_count: number
}

interface FxRates {
  pairs: Record<string, number>
  updated_at: string
}

// ---------- 手续费预设 ----------
type Market = 'CN' | 'HK' | 'US'

type PrefillPlan = {
  signalId: string
  snapshotDate: string
  strategy: string
  action: string
  entryRange: string
  stopLoss: string
  targetPrice: string
  invalidation: string
}

interface CommissionPreset {
  label: string
  desc: string
  calc: (price: number, qty: number) => number
}

const COMMISSION_PRESETS: Record<Market, CommissionPreset[]> = {
  CN: [
    { label: '万三（0.03%）', desc: '买入常见费率', calc: (p, q) => Math.max(p * q * 0.0003, 5) },
    { label: '万一（0.01%）', desc: '低佣平台', calc: (p, q) => Math.max(p * q * 0.0001, 5) },
    { label: '免佣', desc: '零手续费', calc: () => 0 },
  ],
  HK: [
    { label: '0.03% + 税费', desc: '含印花税0.13%', calc: (p, q) => Math.max(p * q * 0.0003, 3) + p * q * 0.0013 },
    { label: '0.05%', desc: '常见佣金', calc: (p, q) => Math.max(p * q * 0.0005, 3) },
    { label: '免佣', desc: '只含税费', calc: (p, q) => p * q * 0.0013 },
  ],
  US: [
    { label: '$0（免佣）', desc: 'Robinhood/富途等', calc: () => 0 },
    { label: '$0.005/股', desc: '盈透证券', calc: (_, q) => Math.max(q * 0.005, 1) },
    { label: '$1/笔', desc: '固定费用', calc: () => 1 },
  ],
}

// ---------- 货币换算 ----------
const CURRENCY_OF: Record<Market, string> = { CN: 'CNY', HK: 'HKD', US: 'USD' }
const CURRENCY_LABEL: Record<string, string> = { CNY: '¥ 人民币', HKD: 'HK$ 港币', USD: '$ 美元' }
const ALL_CURRENCIES = ['CNY', 'HKD', 'USD']

function convertAmount(amount: number, from: string, to: string, pairs: Record<string, number>): number {
  if (from === to) return amount
  const key = `${from}_${to}`
  const rate = pairs[key]
  if (rate) return amount * rate
  // 反向
  const rkey = `${to}_${from}`
  const rrate = pairs[rkey]
  if (rrate) return amount / rrate
  return amount
}

// ---------- 工具 ----------
function fmt(v: number, digits = 2) {
  return v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function compactPrice(v: number | null) {
  if (v == null || Number.isNaN(v)) return '--'
  const abs = Math.abs(v)
  const digits = abs >= 100 ? 2 : abs >= 1 ? 3 : 4
  return v.toFixed(digits).replace(/\.?0+$/, '')
}

function queryNumber(params: URLSearchParams, key: string): number | null {
  const raw = params.get(key)
  if (!raw) return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

function queryText(params: URLSearchParams, key: string) {
  return (params.get(key) || '').trim()
}

function queryMarket(value: string): Market {
  const upper = value.toUpperCase()
  return upper === 'CN' || upper === 'HK' || upper === 'US' ? upper : 'US'
}

function buildSignalNote(params: URLSearchParams, entryRange: string, stopLoss: string, targetPrice: string) {
  const strategy = queryText(params, 'strategy_name') || queryText(params, 'strategy_code') || '策略信号'
  const signalId = queryText(params, 'signal_id')
  const action = queryText(params, 'action_label') || queryText(params, 'action')
  const snapshot = queryText(params, 'snapshot_date')
  const reason = queryText(params, 'reason')
  const invalidation = queryText(params, 'invalidation')
  return [
    `来自机会页: ${strategy}${signalId ? ` #${signalId}` : ''}${snapshot ? ` (${snapshot})` : ''}`,
    action ? `动作: ${action}` : '',
    `买入区间: ${entryRange}`,
    `止损价: ${stopLoss}`,
    `目标价: ${targetPrice}`,
    invalidation ? `失效条件: ${invalidation}` : '',
    reason ? `理由: ${reason}` : '',
  ].filter(Boolean).join('；')
}

function PnlBadge({ value, pct, suffix = '' }: { value: number; pct?: number; suffix?: string }) {
  const isPos = value > 0
  const isNeg = value < 0
  const color = isPos ? 'text-rose-500' : isNeg ? 'text-emerald-500' : 'text-muted-foreground'
  const prefix = isPos ? '+' : ''
  return (
    <span className={`font-mono font-medium ${color}`}>
      {prefix}{fmt(value)}{suffix}
      {pct !== undefined && <span className="ml-1 text-xs opacity-75">({prefix}{fmt(pct)}%)</span>}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    open:    { label: '持仓中', cls: 'bg-blue-500/10 text-blue-600 border-blue-500/20' },
    partial: { label: '部分平', cls: 'bg-amber-500/10 text-amber-600 border-amber-500/20' },
    closed:  { label: '已平仓', cls: 'bg-muted/50 text-muted-foreground border-border' },
  }
  const s = map[status] || map.open
  return <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${s.cls}`}>{s.label}</span>
}

function toLocalDatetimeInput(iso?: string) {
  if (!iso) return new Date().toISOString().slice(0, 16)
  // Convert UTC to local
  const d = new Date(iso)
  const offset = d.getTimezoneOffset()
  const local = new Date(d.getTime() - offset * 60000)
  return local.toISOString().slice(0, 16)
}

function toUTC(localDatetime: string) {
  return new Date(localDatetime).toISOString()
}

const MARKETS: Market[] = ['CN', 'HK', 'US']
const emptyLotForm = { stock_symbol: '', stock_market: 'US' as Market, stock_name: '', quantity: '', buy_price: '', commission: '0', bought_at: new Date().toISOString().slice(0, 16), note: '' }
const emptySellForm = { quantity: '', sell_price: '', sell_currency: 'USD', commission: '0', sold_at: new Date().toISOString().slice(0, 16), note: '' }

// ---------- 手续费预设选择器 ----------
function CommissionPresetPicker({ market, price, qty, onSelect }: { market: Market; price: number; qty: number; onSelect: (v: string) => void }) {
  const presets = COMMISSION_PRESETS[market] || []
  if (!presets.length) return null
  return (
    <div className="mt-1 flex flex-wrap gap-1.5">
      {presets.map(p => {
        const val = price > 0 && qty > 0 ? p.calc(price, qty) : null
        return (
          <button
            key={p.label}
            type="button"
            onClick={() => val !== null && onSelect(String(Math.round(val * 100) / 100))}
            disabled={val === null}
            className="text-[11px] px-2 py-1 rounded-md border border-border/60 bg-accent/40 hover:bg-accent hover:border-primary/30 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            title={p.desc}
          >
            {p.label}{val !== null ? ` ≈ ${fmt(val, 2)}` : ''}
          </button>
        )
      })}
    </div>
  )
}

// ---------- 货币换算小组件 ----------
function FxConverter({ pairs, updatedAt, onRefresh, refreshing }: { pairs: Record<string, number>; updatedAt: string; onRefresh: () => void; refreshing: boolean }) {
  const [amount, setAmount] = useState('1000')
  const [from, setFrom] = useState('USD')
  const n = parseFloat(amount) || 0
  return (
    <div className="rounded-xl bg-accent/20 border border-border/40 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-foreground">汇率换算</span>
        <button onClick={onRefresh} disabled={refreshing} className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors">
          <RefreshCw className={`w-3 h-3 ${refreshing ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>
      <div className="flex items-center gap-2">
        <Input value={amount} onChange={e => setAmount(e.target.value)} className="h-8 w-28 font-mono text-[13px]" />
        <Select value={from} onValueChange={setFrom}>
          <SelectTrigger className="h-8 w-28 text-[12px]"><SelectValue /></SelectTrigger>
          <SelectContent>{ALL_CURRENCIES.map(c => <SelectItem key={c} value={c}>{CURRENCY_LABEL[c]}</SelectItem>)}</SelectContent>
        </Select>
        <span className="text-muted-foreground text-[12px]">≈</span>
      </div>
      <div className="space-y-1">
        {ALL_CURRENCIES.filter(c => c !== from).map(to => {
          const rate = pairs[`${from}_${to}`] || (pairs[`${to}_${from}`] ? 1 / pairs[`${to}_${from}`] : null)
          const result = rate ? n * rate : null
          return (
            <div key={to} className="flex items-center justify-between text-[12px]">
              <span className="text-muted-foreground">{CURRENCY_LABEL[to]}</span>
              <span className="font-mono font-medium">{result !== null ? fmt(result) : '—'}</span>
            </div>
          )
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground/70 border-t border-border/30 pt-1.5">
        {['USD_CNY', 'USD_HKD', 'HKD_CNY'].map(k => pairs[k] ? <span key={k}>{k.replace('_', '→')}: {pairs[k]}</span> : null)}
        <span className="ml-auto">{updatedAt ? new Date(updatedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}</span>
      </div>
    </div>
  )
}

// ---------- 主页面 ----------
export default function RealTradingPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [lots, setLots] = useState<Lot[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [fx, setFx] = useState<FxRates | null>(null)
  const [fxRefreshing, setFxRefreshing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [marketFilter, setMarketFilter] = useState<string>('ALL')
  const [statusFilter, setStatusFilter] = useState<string>('ALL')
  const [displayCurrency, setDisplayCurrency] = useState<string>('原币')
  const [expandedLots, setExpandedLots] = useState<Set<number>>(new Set())
  const [sellsByLot, setSellsByLot] = useState<Record<number, Sell[]>>({})
  const [activeTab, setActiveTab] = useState<'lots' | 'summary'>('lots')

  const [lotDialogOpen, setLotDialogOpen] = useState(false)
  const [lotForm, setLotForm] = useState({ ...emptyLotForm })
  const [editLotId, setEditLotId] = useState<number | null>(null)
  const [prefillPlan, setPrefillPlan] = useState<PrefillPlan | null>(null)

  const [sellDialogOpen, setSellDialogOpen] = useState(false)
  const [sellForm, setSellForm] = useState({ ...emptySellForm })
  const [sellTargetLot, setSellTargetLot] = useState<Lot | null>(null)
  const [editSellId, setEditSellId] = useState<number | null>(null)

  const { toast } = useToast()

  const loadFx = useCallback(async (showRefreshing = false) => {
    if (showRefreshing) setFxRefreshing(true)
    try {
      const data = await fetchAPI<FxRates>('/real-trades/fx-rates')
      setFx(data)
    } catch { /* ignore */ } finally {
      setFxRefreshing(false)
    }
  }, [])

  const loadLots = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (marketFilter !== 'ALL') params.set('market', marketFilter)
      if (statusFilter !== 'ALL') params.set('status', statusFilter.toLowerCase())
      const data = await fetchAPI<Lot[]>(`/real-trades/lots?${params}`)
      setLots(data)
    } finally { setLoading(false) }
  }

  const loadSummary = async () => {
    try {
      const params = new URLSearchParams()
      if (marketFilter !== 'ALL') params.set('market', marketFilter)
      const data = await fetchAPI<Summary>(`/real-trades/summary?${params}`)
      setSummary(data)
    } catch { /* ignore */ }
  }

  const loadSells = async (lotId: number) => {
    const data = await fetchAPI<Sell[]>(`/real-trades/sells?lot_id=${lotId}`)
    setSellsByLot(prev => ({ ...prev, [lotId]: data }))
  }

  useEffect(() => { loadLots(); loadSummary() }, [marketFilter, statusFilter])
  useEffect(() => { loadFx() }, [])

  useEffect(() => {
    if (searchParams.get('prefill') !== 'opportunity-buy') return
    const symbol = queryText(searchParams, 'symbol').toUpperCase()
    if (!symbol) return
    const market = queryMarket(queryText(searchParams, 'market'))
    const entryLow = queryNumber(searchParams, 'entry_low')
    const entryHigh = queryNumber(searchParams, 'entry_high')
    const buyPrice = queryNumber(searchParams, 'buy_price') ?? (entryLow != null && entryHigh != null ? (entryLow + entryHigh) / 2 : entryLow ?? entryHigh)
    const stopLoss = queryNumber(searchParams, 'stop_loss')
    const targetPrice = queryNumber(searchParams, 'target_price')
    const entryRange = `${compactPrice(entryLow)} ~ ${compactPrice(entryHigh)}`
    const stopText = compactPrice(stopLoss)
    const targetText = compactPrice(targetPrice)

    setLotForm({
      ...emptyLotForm,
      stock_symbol: symbol,
      stock_market: market,
      stock_name: queryText(searchParams, 'name'),
      buy_price: buyPrice != null ? compactPrice(buyPrice) : '',
      note: buildSignalNote(searchParams, entryRange, stopText, targetText),
    })
    setPrefillPlan({
      signalId: queryText(searchParams, 'signal_id'),
      snapshotDate: queryText(searchParams, 'snapshot_date'),
      strategy: queryText(searchParams, 'strategy_name') || queryText(searchParams, 'strategy_code'),
      action: queryText(searchParams, 'action_label') || queryText(searchParams, 'action'),
      entryRange,
      stopLoss: stopText,
      targetPrice: targetText,
      invalidation: queryText(searchParams, 'invalidation'),
    })
    setEditLotId(null)
    setLotDialogOpen(true)
    navigate('/real-trading', { replace: true })
  }, [navigate, searchParams])

  // 换算盈亏金额到目标货币
  const convertPnl = (pnl: number, fromMarket: string): { value: number; currency: string } => {
    const fromCurrency = CURRENCY_OF[fromMarket as Market] || 'CNY'
    if (displayCurrency === '原币' || !fx) return { value: pnl, currency: fromCurrency }
    const converted = convertAmount(pnl, fromCurrency, displayCurrency, fx.pairs)
    return { value: converted, currency: displayCurrency }
  }

  const toggleExpand = async (lotId: number) => {
    const next = new Set(expandedLots)
    if (next.has(lotId)) { next.delete(lotId) } else {
      next.add(lotId)
      if (!sellsByLot[lotId]) await loadSells(lotId)
    }
    setExpandedLots(next)
  }

  // 买入
  const openLotDialog = (lot?: Lot) => {
    if (lot) {
      setLotForm({ stock_symbol: lot.stock_symbol, stock_market: lot.stock_market as Market, stock_name: lot.stock_name, quantity: String(lot.quantity), buy_price: String(lot.buy_price), commission: String(lot.commission), bought_at: toLocalDatetimeInput(lot.bought_at), note: lot.note })
      setEditLotId(lot.id)
      setPrefillPlan(null)
    } else {
      setLotForm({ ...emptyLotForm })
      setEditLotId(null)
      setPrefillPlan(null)
    }
    setLotDialogOpen(true)
  }

  const saveLot = async () => {
    const payload = { stock_symbol: lotForm.stock_symbol.toUpperCase(), stock_market: lotForm.stock_market, stock_name: lotForm.stock_name, quantity: Number(lotForm.quantity), buy_price: Number(lotForm.buy_price), commission: Number(lotForm.commission), bought_at: toUTC(lotForm.bought_at), note: lotForm.note }
    try {
      if (editLotId) {
        await fetchAPI(`/real-trades/lots/${editLotId}`, { method: 'PUT', body: JSON.stringify(payload) })
        toast('已更新买入记录', 'success')
      } else {
        await fetchAPI('/real-trades/lots', { method: 'POST', body: JSON.stringify(payload) })
        toast('买入记录已添加', 'success')
      }
      setLotDialogOpen(false)
      setPrefillPlan(null)
      await loadLots(); await loadSummary()
    } catch (e) { toast(e instanceof Error ? e.message : '保存失败', 'error') }
  }

  const deleteLot = async (id: number) => {
    if (!confirm('确定删除该买入记录？（需先删除关联卖出记录）')) return
    try {
      await fetchAPI(`/real-trades/lots/${id}`, { method: 'DELETE' })
      toast('已删除', 'success')
      await loadLots(); await loadSummary()
    } catch (e) { toast(e instanceof Error ? e.message : '删除失败', 'error') }
  }

  // 卖出
  const openSellDialog = (lot: Lot, sell?: Sell) => {
    setSellTargetLot(lot)
    if (sell) {
      setSellForm({ quantity: String(sell.quantity), sell_price: String(sell.sell_price), sell_currency: sell.sell_currency || CURRENCY_OF[lot.stock_market as Market] || 'USD', commission: String(sell.commission), sold_at: toLocalDatetimeInput(sell.sold_at), note: sell.note })
      setEditSellId(sell.id)
    } else {
      const defaultCurrency = CURRENCY_OF[lot.stock_market as Market] || 'USD'
      setSellForm({ ...emptySellForm, sell_currency: defaultCurrency, quantity: String(lot.remaining_qty), sold_at: new Date().toISOString().slice(0, 16) })
      setEditSellId(null)
    }
    setSellDialogOpen(true)
  }

  const saveSell = async () => {
    if (!sellTargetLot) return
    const payload = { lot_id: sellTargetLot.id, quantity: Number(sellForm.quantity), sell_price: Number(sellForm.sell_price), sell_currency: sellForm.sell_currency, commission: Number(sellForm.commission), sold_at: toUTC(sellForm.sold_at), note: sellForm.note }
    try {
      if (editSellId) {
        await fetchAPI(`/real-trades/sells/${editSellId}`, { method: 'PUT', body: JSON.stringify(payload) })
        toast('已更新卖出记录', 'success')
      } else {
        await fetchAPI('/real-trades/sells', { method: 'POST', body: JSON.stringify(payload) })
        toast('卖出记录已添加', 'success')
      }
      setSellDialogOpen(false)
      await loadLots(); await loadSummary()
      if (expandedLots.has(sellTargetLot.id)) await loadSells(sellTargetLot.id)
    } catch (e) { toast(e instanceof Error ? e.message : '保存失败', 'error') }
  }

  const deleteSell = async (sell: Sell) => {
    if (!confirm('确定删除该卖出记录？')) return
    try {
      await fetchAPI(`/real-trades/sells/${sell.id}`, { method: 'DELETE' })
      toast('已删除', 'success')
      await loadLots(); await loadSummary()
      await loadSells(sell.lot_id)
    } catch (e) { toast(e instanceof Error ? e.message : '删除失败', 'error') }
  }

  const lotFormValid = lotForm.stock_symbol && Number(lotForm.quantity) > 0 && Number(lotForm.buy_price) > 0 && lotForm.bought_at
  const sellFormValid = Number(sellForm.quantity) > 0 && Number(sellForm.sell_price) > 0 && sellForm.sold_at

  // 实时预览盈亏（卖出弹窗）
  const previewPnl = (() => {
    if (!sellTargetLot || !Number(sellForm.sell_price) || !Number(sellForm.quantity)) return null
    const qty = Number(sellForm.quantity)
    const sp = Number(sellForm.sell_price)
    const sc = Number(sellForm.commission || 0)
    const bc = sellTargetLot.commission * (qty / sellTargetLot.quantity)
    const lotCurrency = CURRENCY_OF[sellTargetLot.stock_market as Market] || 'CNY'
    const sellCurrency = sellForm.sell_currency || lotCurrency
    // 卖出价换算到 lot 原币后再算盈亏
    const spInLot = fx && sellCurrency !== lotCurrency
      ? convertAmount(sp, sellCurrency, lotCurrency, fx.pairs)
      : sp
    const cost = sellTargetLot.buy_price * qty + bc + sc
    const pnl = spInLot * qty - cost
    const pct = cost > 0 ? pnl / cost * 100 : 0
    return { pnl, pct, currency: lotCurrency }
  })()

  const currencyOptions = ['原币', ...ALL_CURRENCIES]

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="card p-4 md:p-6">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-bold text-foreground">真实交易记录</h2>
            <p className="text-[12px] text-muted-foreground mt-0.5">记录买卖操作，统计实际盈亏</p>
          </div>
          <Button size="sm" onClick={() => openLotDialog()}>
            <Plus className="w-3.5 h-3.5" /> 记录买入
          </Button>
        </div>

        {/* 汇总卡片 */}
        {summary && (
          <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="rounded-xl bg-accent/30 p-3">
              <div className="text-[11px] text-muted-foreground">已实现盈亏（原币）</div>
              <div className="mt-1 text-[16px] font-bold"><PnlBadge value={summary.total_pnl} /></div>
              <div className="text-[10px] text-muted-foreground mt-0.5">{summary.total_closed_trades} 笔已平仓</div>
            </div>
            <div className="rounded-xl bg-accent/30 p-3">
              <div className="text-[11px] text-muted-foreground">胜率</div>
              <div className="mt-1 text-[16px] font-bold text-foreground">{fmt(summary.win_rate)}%</div>
              <div className="text-[10px] text-muted-foreground">{summary.win_count} 胜 / {summary.lose_count} 负</div>
            </div>
            <div className="rounded-xl bg-accent/30 p-3">
              <div className="text-[11px] text-muted-foreground">平均盈亏率</div>
              <div className={`mt-1 text-[16px] font-bold font-mono ${summary.avg_pnl_pct > 0 ? 'text-rose-500' : summary.avg_pnl_pct < 0 ? 'text-emerald-500' : 'text-foreground'}`}>
                {summary.avg_pnl_pct > 0 ? '+' : ''}{fmt(summary.avg_pnl_pct)}%
              </div>
            </div>
            <div className="rounded-xl bg-accent/30 p-3">
              <div className="text-[11px] text-muted-foreground">平均持仓</div>
              <div className="mt-1 text-[16px] font-bold text-foreground">{fmt(summary.avg_holding_days, 0)} 天</div>
              <div className="text-[10px] text-muted-foreground">持仓中 {summary.open_lots_count} 笔</div>
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 左侧主内容 */}
        <div className="lg:col-span-2 space-y-4">
          {/* Tab + 过滤器 */}
          <div className="flex flex-wrap gap-2 items-center">
            {(['lots', 'summary'] as const).map(tab => (
              <button key={tab} onClick={() => setActiveTab(tab)}
                className={`px-4 py-1.5 rounded-full text-[12px] font-medium transition-colors border ${activeTab === tab ? 'bg-primary text-white border-primary' : 'border-border text-muted-foreground hover:text-foreground'}`}
              >
                {tab === 'lots' ? '买卖明细' : '按股票汇总'}
              </button>
            ))}
            <div className="ml-auto flex items-center gap-2">
              <Select value={displayCurrency} onValueChange={setDisplayCurrency}>
                <SelectTrigger className="h-8 w-[90px] text-[12px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {currencyOptions.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                </SelectContent>
              </Select>
              <Select value={marketFilter} onValueChange={setMarketFilter}>
                <SelectTrigger className="h-8 w-[80px] text-[12px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">全部</SelectItem>
                  {MARKETS.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                </SelectContent>
              </Select>
              {activeTab === 'lots' && (
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                  <SelectTrigger className="h-8 w-[90px] text-[12px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ALL">全部状态</SelectItem>
                    <SelectItem value="open">持仓中</SelectItem>
                    <SelectItem value="partial">部分平</SelectItem>
                    <SelectItem value="closed">已平仓</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>

          {/* 买卖明细 */}
          {activeTab === 'lots' && (
            <div className="card p-4 md:p-5">
              {loading ? (
                <div className="flex justify-center py-12"><span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" /></div>
              ) : lots.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground text-[13px]">暂无记录，点击"记录买入"开始</div>
              ) : (
                <div className="space-y-2">
                  {lots.map(lot => {
                    const currency = CURRENCY_OF[lot.stock_market as Market] || 'CNY'
                    const { value: dispPnl, currency: dispCurrency } = convertPnl(lot.realized_pnl, lot.stock_market)
                    return (
                      <div key={lot.id} className="rounded-xl border border-border/40 overflow-hidden">
                        <div className="flex items-center gap-2 p-3 bg-accent/20 hover:bg-accent/30 transition-colors">
                          <button onClick={() => toggleExpand(lot.id)} className="text-muted-foreground hover:text-foreground flex-shrink-0">
                            {expandedLots.has(lot.id) ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                          </button>
                          <div className="flex-1 min-w-0 grid grid-cols-2 md:grid-cols-6 gap-x-3 gap-y-0.5 items-center">
                            <div className="col-span-2 md:col-span-1 flex items-center gap-1.5 flex-wrap">
                              <span className="font-mono font-bold text-[13px] text-foreground">{lot.stock_symbol}</span>
                              <span className="text-[10px] text-muted-foreground border border-border/50 rounded px-1">{lot.stock_market}</span>
                              <StatusBadge status={lot.status} />
                            </div>
                            <div className="text-[12px] text-muted-foreground truncate">{lot.stock_name || '-'}</div>
                            <div className="text-[12px]">
                              <span className="text-muted-foreground text-[10px]">均价 </span>
                              <span className="font-mono">{fmt(lot.buy_price)}</span>
                              <span className="text-[10px] text-muted-foreground ml-0.5">{currency}</span>
                            </div>
                            <div className="text-[12px]">
                              <span className="text-muted-foreground text-[10px]">持/总 </span>
                              <span className="font-mono">{lot.remaining_qty}/{lot.quantity}</span>
                            </div>
                            <div className="text-[12px]">
                              {lot.sell_count > 0
                                ? <><PnlBadge value={dispPnl} />{displayCurrency !== '原币' && <span className="text-[10px] text-muted-foreground ml-0.5">{dispCurrency}</span>}</>
                                : <span className="text-muted-foreground text-[11px]">未平仓</span>}
                            </div>
                            <div className="text-[11px] text-muted-foreground">{lot.bought_at?.slice(0, 10)}</div>
                          </div>
                          <div className="flex items-center gap-1 flex-shrink-0">
                            {lot.status !== 'closed' && (
                              <Button size="sm" variant="ghost" className="h-7 text-[11px] px-2" onClick={() => openSellDialog(lot)}>
                                <DollarSign className="w-3 h-3" /><span className="hidden sm:inline ml-0.5">卖出</span>
                              </Button>
                            )}
                            <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => openLotDialog(lot)}>
                              <Pencil className="w-3 h-3" />
                            </Button>
                            <Button size="sm" variant="ghost" className="h-7 w-7 p-0 hover:text-destructive" onClick={() => deleteLot(lot.id)}>
                              <Trash2 className="w-3 h-3" />
                            </Button>
                          </div>
                        </div>

                        {/* 卖出子行 */}
                        {expandedLots.has(lot.id) && (
                          <div className="border-t border-border/30 bg-background/50">
                            {!(sellsByLot[lot.id]?.length) ? (
                              <div className="text-[12px] text-muted-foreground px-10 py-2">暂无卖出记录</div>
                            ) : sellsByLot[lot.id].map(sell => {
                              const sellCurr = sell.sell_currency || CURRENCY_OF[sell.stock_market as Market] || 'CNY'
                              const { value: sv, currency: sc2 } = convertPnl(sell.pnl, sell.stock_market)
                              return (
                                <div key={sell.id} className="flex items-center gap-2 px-10 py-2 border-b border-border/20 last:border-0 hover:bg-accent/10">
                                  <div className="flex-1 grid grid-cols-2 md:grid-cols-5 gap-x-3 items-center text-[12px]">
                                    <div>
                                      <span className="text-muted-foreground text-[10px]">卖出 </span>
                                      <span className="font-mono">{fmt(sell.sell_price)}</span>
                                      <span className="text-muted-foreground text-[10px] ml-0.5">{sellCurr} × {sell.quantity}</span>
                                    </div>
                                    <div>
                                      <PnlBadge value={sv} pct={sell.pnl_pct} />
                                      {displayCurrency !== '原币' && <span className="text-[10px] text-muted-foreground ml-0.5">{sc2}</span>}
                                    </div>
                                    <div className="text-muted-foreground">持 {sell.holding_days} 天</div>
                                    <div className="text-muted-foreground">{sell.sold_at?.slice(0, 10)}</div>
                                    <div className="text-muted-foreground text-[11px] truncate">{sell.note}</div>
                                  </div>
                                  <div className="flex items-center gap-1 flex-shrink-0">
                                    <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => openSellDialog(lot, sell)}>
                                      <Pencil className="w-3 h-3" />
                                    </Button>
                                    <Button size="sm" variant="ghost" className="h-6 w-6 p-0 hover:text-destructive" onClick={() => deleteSell(sell)}>
                                      <Trash2 className="w-3 h-3" />
                                    </Button>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* 按股票汇总 */}
          {activeTab === 'summary' && summary && (
            <div className="card p-4 md:p-5">
              {summary.by_stock.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground text-[13px]">暂无已平仓记录</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="text-left py-2 px-2 text-muted-foreground font-medium">股票</th>
                        <th className="text-right py-2 px-2 text-muted-foreground font-medium">市场</th>
                        <th className="text-right py-2 px-2 text-muted-foreground font-medium">次数</th>
                        <th className="text-right py-2 px-2 text-muted-foreground font-medium">胜率</th>
                        <th className="text-right py-2 px-2 text-muted-foreground font-medium">总盈亏</th>
                        {displayCurrency !== '原币' && fx && <th className="text-right py-2 px-2 text-muted-foreground font-medium">≈ {displayCurrency}</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {summary.by_stock.map(s => {
                        const { value: conv } = convertPnl(s.total_pnl, s.market)
                        return (
                          <tr key={`${s.symbol}-${s.market}`} className="border-b border-border/20 hover:bg-accent/10">
                            <td className="py-2.5 px-2">
                              <span className="font-mono font-bold text-foreground">{s.symbol}</span>
                              {s.name && <span className="ml-2 text-muted-foreground">{s.name}</span>}
                            </td>
                            <td className="text-right py-2.5 px-2 text-muted-foreground">{s.market}</td>
                            <td className="text-right py-2.5 px-2">{s.trade_count}</td>
                            <td className="text-right py-2.5 px-2">{fmt(s.win_rate)}%</td>
                            <td className="text-right py-2.5 px-2"><PnlBadge value={s.total_pnl} /></td>
                            {displayCurrency !== '原币' && fx && (
                              <td className="text-right py-2.5 px-2"><PnlBadge value={conv} /></td>
                            )}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {(summary.best_trade || summary.worst_trade) && (
                <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
                  {summary.best_trade && (
                    <div className="rounded-xl bg-rose-500/5 border border-rose-500/15 p-3">
                      <div className="flex items-center gap-1.5 text-[11px] text-rose-600 font-medium mb-1"><Trophy className="w-3.5 h-3.5" /> 最佳一笔</div>
                      <div className="font-mono font-bold text-[13px]">{summary.best_trade.stock_symbol}</div>
                      <div className="text-[12px] mt-0.5"><PnlBadge value={summary.best_trade.pnl} pct={summary.best_trade.pnl_pct} /></div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">持仓 {summary.best_trade.holding_days} 天 · {summary.best_trade.sold_at?.slice(0, 10)}</div>
                    </div>
                  )}
                  {summary.worst_trade && (
                    <div className="rounded-xl bg-emerald-500/5 border border-emerald-500/15 p-3">
                      <div className="flex items-center gap-1.5 text-[11px] text-emerald-600 font-medium mb-1"><BarChart3 className="w-3.5 h-3.5" /> 最差一笔</div>
                      <div className="font-mono font-bold text-[13px]">{summary.worst_trade.stock_symbol}</div>
                      <div className="text-[12px] mt-0.5"><PnlBadge value={summary.worst_trade.pnl} pct={summary.worst_trade.pnl_pct} /></div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">持仓 {summary.worst_trade.holding_days} 天 · {summary.worst_trade.sold_at?.slice(0, 10)}</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 右侧：汇率换算 */}
        <div className="space-y-4">
          {fx ? (
            <FxConverter pairs={fx.pairs} updatedAt={fx.updated_at} onRefresh={() => loadFx(true)} refreshing={fxRefreshing} />
          ) : (
            <div className="rounded-xl bg-accent/20 border border-border/40 p-4 text-center text-[12px] text-muted-foreground">
              <RefreshCw className="w-4 h-4 mx-auto mb-1 animate-spin" />加载汇率中…
            </div>
          )}
          {/* 常用汇率速查 */}
          {fx && (
            <div className="rounded-xl bg-accent/20 border border-border/40 p-3 space-y-1.5 text-[12px]">
              <div className="font-medium text-foreground mb-2">实时汇率</div>
              {[
                { label: '美元 → 人民币', key: 'USD_CNY', unit: '¥' },
                { label: '美元 → 港币', key: 'USD_HKD', unit: 'HK$' },
                { label: '港币 → 人民币', key: 'HKD_CNY', unit: '¥' },
              ].map(row => (
                <div key={row.key} className="flex items-center justify-between">
                  <span className="text-muted-foreground">{row.label}</span>
                  <span className="font-mono font-medium">{row.unit} {fx.pairs[row.key] ?? '—'}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 买入弹窗 */}
      <Dialog
        open={lotDialogOpen}
        onOpenChange={(open) => {
          setLotDialogOpen(open)
          if (!open) setPrefillPlan(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editLotId ? '编辑买入记录' : '记录买入'}</DialogTitle>
            <DialogDescription>支持同一股票多笔分仓买入</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-1">
            {prefillPlan && !editLotId && (
              <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-[11px]">
                <div className="mb-2 font-medium text-foreground">机会页执行计划已带入</div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-muted-foreground">
                  <div>策略: {prefillPlan.strategy || '--'}</div>
                  <div>动作: {prefillPlan.action || '--'}</div>
                  <div>入场: {prefillPlan.entryRange}</div>
                  <div>止损: {prefillPlan.stopLoss}</div>
                  <div>目标: {prefillPlan.targetPrice}</div>
                  <div>信号: {prefillPlan.signalId || prefillPlan.snapshotDate || '--'}</div>
                  {prefillPlan.invalidation && <div className="col-span-2">失效: {prefillPlan.invalidation}</div>}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>股票代码</Label>
                <Input value={lotForm.stock_symbol} onChange={e => setLotForm({ ...lotForm, stock_symbol: e.target.value.toUpperCase() })} placeholder="如 NVDA" className="font-mono" />
              </div>
              <div>
                <Label>市场</Label>
                <Select value={lotForm.stock_market} onValueChange={v => setLotForm({ ...lotForm, stock_market: v as Market })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{MARKETS.map(m => <SelectItem key={m} value={m}>{m} · {CURRENCY_LABEL[CURRENCY_OF[m]]}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label>股票名称 <span className="text-muted-foreground font-normal">(选填)</span></Label>
              <Input value={lotForm.stock_name} onChange={e => setLotForm({ ...lotForm, stock_name: e.target.value })} placeholder="如 英伟达" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>买入价 <span className="text-muted-foreground font-normal text-[10px]">({CURRENCY_OF[lotForm.stock_market]})</span></Label>
                <Input type="number" value={lotForm.buy_price} onChange={e => setLotForm({ ...lotForm, buy_price: e.target.value })} placeholder="0.00" className="font-mono" />
              </div>
              <div>
                <Label>数量（股）</Label>
                <Input type="number" value={lotForm.quantity} onChange={e => setLotForm({ ...lotForm, quantity: e.target.value })} placeholder="100" className="font-mono" />
              </div>
            </div>
            <div>
              <Label>手续费 <span className="text-muted-foreground font-normal text-[10px]">({CURRENCY_OF[lotForm.stock_market]})</span></Label>
              <Input type="number" value={lotForm.commission} onChange={e => setLotForm({ ...lotForm, commission: e.target.value })} placeholder="0" className="font-mono" />
              <CommissionPresetPicker
                market={lotForm.stock_market}
                price={Number(lotForm.buy_price)}
                qty={Number(lotForm.quantity)}
                onSelect={v => setLotForm({ ...lotForm, commission: v })}
              />
            </div>
            <div>
              <Label>买入日期</Label>
              <Input type="datetime-local" value={lotForm.bought_at} onChange={e => setLotForm({ ...lotForm, bought_at: e.target.value })} />
            </div>
            <div>
              <Label>备注 <span className="text-muted-foreground font-normal">(选填)</span></Label>
              <Input value={lotForm.note} onChange={e => setLotForm({ ...lotForm, note: e.target.value })} placeholder="如：止损位 90，目标价 150" />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setLotDialogOpen(false)}>取消</Button>
              <Button onClick={saveLot} disabled={!lotFormValid}>{editLotId ? '保存' : '记录'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 卖出弹窗 */}
      <Dialog open={sellDialogOpen} onOpenChange={setSellDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editSellId ? '编辑卖出' : `记录卖出 · ${sellTargetLot?.stock_symbol}`}</DialogTitle>
            <DialogDescription>
              {sellTargetLot && !editSellId && `剩余 ${sellTargetLot.remaining_qty} 股 · 买入均价 ${fmt(sellTargetLot.buy_price)} ${CURRENCY_OF[sellTargetLot.stock_market as Market]}`}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-1">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>卖出价</Label>
                <Input type="number" value={sellForm.sell_price} onChange={e => setSellForm({ ...sellForm, sell_price: e.target.value })} placeholder="0.00" className="font-mono" />
              </div>
              <div>
                <Label>货币</Label>
                <Select value={sellForm.sell_currency} onValueChange={v => setSellForm({ ...sellForm, sell_currency: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{ALL_CURRENCIES.map(c => <SelectItem key={c} value={c}>{CURRENCY_LABEL[c]}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label>数量（股）</Label>
              <Input type="number" value={sellForm.quantity} onChange={e => setSellForm({ ...sellForm, quantity: e.target.value })} placeholder={String(sellTargetLot?.remaining_qty || '')} className="font-mono" />
            </div>
            <div>
              <Label>手续费 <span className="text-muted-foreground font-normal text-[10px]">({sellForm.sell_currency})</span></Label>
              <Input type="number" value={sellForm.commission} onChange={e => setSellForm({ ...sellForm, commission: e.target.value })} placeholder="0" className="font-mono" />
              {sellTargetLot && (
                <CommissionPresetPicker
                  market={sellTargetLot.stock_market as Market}
                  price={Number(sellForm.sell_price)}
                  qty={Number(sellForm.quantity)}
                  onSelect={v => setSellForm({ ...sellForm, commission: v })}
                />
              )}
            </div>
            <div>
              <Label>卖出日期</Label>
              <Input type="datetime-local" value={sellForm.sold_at} onChange={e => setSellForm({ ...sellForm, sold_at: e.target.value })} />
            </div>

            {/* 盈亏预览 */}
            {previewPnl && (
              <div className="rounded-lg bg-accent/30 px-3 py-2.5 space-y-1">
                <div className="flex items-center justify-between text-[12px]">
                  <span className="text-muted-foreground">预计盈亏（{previewPnl.currency}）</span>
                  <PnlBadge value={previewPnl.pnl} pct={previewPnl.pct} />
                </div>
                {fx && ALL_CURRENCIES.filter(c => c !== previewPnl.currency).map(c => (
                  <div key={c} className="flex items-center justify-between text-[12px]">
                    <span className="text-muted-foreground">≈ {CURRENCY_LABEL[c]}</span>
                    <PnlBadge value={convertAmount(previewPnl.pnl, previewPnl.currency, c, fx.pairs)} />
                  </div>
                ))}
              </div>
            )}

            <div>
              <Label>备注 <span className="text-muted-foreground font-normal">(选填)</span></Label>
              <Input value={sellForm.note} onChange={e => setSellForm({ ...sellForm, note: e.target.value })} placeholder="如：止盈离场" />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setSellDialogOpen(false)}>取消</Button>
              <Button onClick={saveSell} disabled={!sellFormValid}>{editSellId ? '保存' : '记录卖出'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
