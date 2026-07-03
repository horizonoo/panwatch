import { useEffect, useMemo, useState } from 'react'
import { Brain, CheckCircle2, Loader2, RefreshCw, ShieldAlert, Sparkles, TrendingDown, TrendingUp } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { learningApi, type LearningSummary, type LearningRule } from '@panwatch/api'

const pct = (v?: number | null) => v == null ? '--' : `${(v * 100).toFixed(1)}%`
const signed = (v?: number | null) => v == null ? '--' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`

function Stat({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md border border-border/60 bg-card px-3 py-2">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`mt-1 text-[16px] font-bold tabular-nums ${tone}`}>{value}</div>
    </div>
  )
}

function severityClass(severity?: string) {
  if (severity === 'block') return 'border-red-500/30 bg-red-500/10 text-red-400'
  if (severity === 'warn') return 'border-amber-500/30 bg-amber-500/10 text-amber-500'
  return 'border-green-500/30 bg-green-500/10 text-green-500'
}

function RuleCard({ rule }: { rule: LearningRule }) {
  return (
    <div className="rounded-md border border-border/60 bg-card p-3">
      <div className="mb-1 flex items-start justify-between gap-2">
        <div className="text-[13px] font-semibold">{rule.title}</div>
        <span className={`shrink-0 rounded border px-2 py-0.5 text-[10px] ${severityClass(rule.severity)}`}>
          {rule.severity === 'block' ? '拦截' : rule.severity === 'warn' ? '警告' : '提示'}
        </span>
      </div>
      <div className="text-[12px] leading-relaxed text-muted-foreground">{rule.recommendation}</div>
      <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
        {rule.stock_market && <span className="rounded bg-muted/50 px-2 py-0.5">{rule.stock_market}</span>}
        {rule.strategy_code && <span className="rounded bg-muted/50 px-2 py-0.5">{rule.strategy_code}</span>}
        <span className="rounded bg-muted/50 px-2 py-0.5">{rule.scope_type}:{rule.scope_key}</span>
      </div>
    </div>
  )
}

export default function LearningLoopPage() {
  const [days, setDays] = useState('90')
  const [data, setData] = useState<LearningSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState({
    stock_symbol: '',
    stock_market: 'US',
    strategy_code: '',
    action_taken: '',
    result: 'unknown',
    pnl_pct: '',
    mistake_tags: '',
    thesis: '',
    improvement: '',
  })

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setData(await learningApi.summary(Number(days)))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  const rebuild = async () => {
    setSaving(true)
    setError('')
    try {
      await learningApi.rebuildRules(Number(days))
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      setSaving(false)
    }
  }

  const saveReview = async () => {
    if (!form.stock_symbol.trim()) return
    setSaving(true)
    setError('')
    try {
      await learningApi.createReview({
        source: 'manual',
        stock_symbol: form.stock_symbol.trim().toUpperCase(),
        stock_market: form.stock_market,
        strategy_code: form.strategy_code,
        action_taken: form.action_taken,
        result: form.result,
        pnl_pct: form.pnl_pct.trim() ? Number(form.pnl_pct) : null,
        mistake_tags: form.mistake_tags.split(/[,\s，、]+/).map(x => x.trim()).filter(Boolean),
        thesis: form.thesis,
        improvement: form.improvement,
      })
      setForm({ ...form, stock_symbol: '', action_taken: '', pnl_pct: '', mistake_tags: '', thesis: '', improvement: '' })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => { load() }, [])

  const overview = data?.overview
  const topRules = useMemo(() => (data?.active_rules?.length ? data.active_rules : data?.candidate_rules || []).slice(0, 8), [data])

  return (
    <div className="page-container pb-10">
      <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-[20px] font-bold tracking-tight md:text-[22px]">进化</h1>
          <p className="mt-1 text-[12px] text-muted-foreground">从模拟盘、真实交易和人工复盘里提炼规则，减少重复犯错。</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={days} onValueChange={setDays}>
            <SelectTrigger className="h-9 w-28"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="30">30 天</SelectItem>
              <SelectItem value="90">90 天</SelectItem>
              <SelectItem value="180">180 天</SelectItem>
              <SelectItem value="365">365 天</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={load} disabled={loading} className="h-9 text-[12px]">
            {loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
            刷新
          </Button>
          <Button onClick={rebuild} disabled={saving} className="h-9 text-[12px]">
            {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Brain className="mr-1.5 h-3.5 w-3.5" />}
            生成规则
          </Button>
        </div>
      </div>

      {error && <div className="mb-4 rounded-md bg-destructive/10 p-3 text-[12px] text-destructive">{error}</div>}

      <div className="mb-4 grid gap-3 md:grid-cols-6">
        <Stat label="模拟盘笔数" value={`${overview?.paper_trades ?? 0}`} />
        <Stat label="模拟盘胜率" value={pct(overview?.paper_win_rate)} />
        <Stat label="模拟盘均值" value={signed(overview?.paper_avg_return_pct)} tone={(overview?.paper_avg_return_pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-400'} />
        <Stat label="真实卖出" value={`${overview?.real_sells ?? 0}`} />
        <Stat label="策略样本" value={`${overview?.strategy_outcomes ?? 0}`} />
        <Stat label="策略均值" value={signed(overview?.strategy_avg_return_pct)} tone={(overview?.strategy_avg_return_pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-400'} />
      </div>

      <div className="mb-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold">
            <ShieldAlert className="h-4 w-4 text-amber-500" />
            当前生效/候选规则
          </div>
          <div className="space-y-2">
            {topRules.length ? topRules.map((r, i) => <RuleCard key={`${r.scope_key}-${i}`} rule={r} />) : (
              <div className="rounded-md bg-muted/40 p-4 text-[12px] text-muted-foreground">暂无规则。交易样本增加后点击「生成规则」。</div>
            )}
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold">
            <Sparkles className="h-4 w-4 text-primary" />
            策略表现
          </div>
          <div className="space-y-2">
            {(data?.strategies || []).slice(0, 8).map(row => (
              <div key={`${row.strategy_code}-${row.market}`} className="grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded-md bg-muted/40 px-3 py-2 text-[11px]">
                <div>
                  <div className="font-medium">{row.market || 'ALL'} · {row.strategy_code}</div>
                  <div className="text-muted-foreground">{row.samples} 样本 · 目标 {pct(row.target_hit_rate)} · 止损 {pct(row.stop_hit_rate)}</div>
                </div>
                <div className="text-right">
                  <div className="text-muted-foreground">胜率</div>
                  <div className="font-semibold">{pct(row.win_rate)}</div>
                </div>
                <div className="text-right">
                  <div className="text-muted-foreground">均值</div>
                  <div className={`font-semibold ${row.avg_return_pct >= 0 ? 'text-green-500' : 'text-red-400'}`}>{signed(row.avg_return_pct)}</div>
                </div>
              </div>
            ))}
            {!data?.strategies?.length && <div className="rounded-md bg-muted/40 p-4 text-[12px] text-muted-foreground">暂无足够策略结果。</div>}
          </div>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold">
            <CheckCircle2 className="h-4 w-4 text-green-500" />
            记录人工复盘
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            <input value={form.stock_symbol} onChange={e => setForm({ ...form, stock_symbol: e.target.value.toUpperCase() })} placeholder="股票代码" className="h-9 rounded-md border border-border bg-background px-3 text-[12px]" />
            <Select value={form.stock_market} onValueChange={v => setForm({ ...form, stock_market: v })}>
              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="US">美股</SelectItem>
                <SelectItem value="HK">港股</SelectItem>
                <SelectItem value="CN">A股</SelectItem>
              </SelectContent>
            </Select>
            <Select value={form.result} onValueChange={v => setForm({ ...form, result: v })}>
              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="win">盈利</SelectItem>
                <SelectItem value="loss">亏损</SelectItem>
                <SelectItem value="flat">打平</SelectItem>
                <SelectItem value="unknown">未定</SelectItem>
              </SelectContent>
            </Select>
            <input value={form.strategy_code} onChange={e => setForm({ ...form, strategy_code: e.target.value })} placeholder="策略代码" className="h-9 rounded-md border border-border bg-background px-3 text-[12px]" />
            <input value={form.action_taken} onChange={e => setForm({ ...form, action_taken: e.target.value })} placeholder="实际动作" className="h-9 rounded-md border border-border bg-background px-3 text-[12px]" />
            <input value={form.pnl_pct} onChange={e => setForm({ ...form, pnl_pct: e.target.value })} placeholder="收益率%" className="h-9 rounded-md border border-border bg-background px-3 text-[12px]" />
          </div>
          <textarea value={form.thesis} onChange={e => setForm({ ...form, thesis: e.target.value })} placeholder="当时的交易逻辑" className="mt-2 min-h-20 w-full rounded-md border border-border bg-background px-3 py-2 text-[12px]" />
          <textarea value={form.improvement} onChange={e => setForm({ ...form, improvement: e.target.value })} placeholder="下次应如何改进" className="mt-2 min-h-20 w-full rounded-md border border-border bg-background px-3 py-2 text-[12px]" />
          <input value={form.mistake_tags} onChange={e => setForm({ ...form, mistake_tags: e.target.value })} placeholder="标签：追高, 仓位过重, 没等确认" className="mt-2 h-9 w-full rounded-md border border-border bg-background px-3 text-[12px]" />
          <div className="mt-3 flex justify-end">
            <Button onClick={saveReview} disabled={saving || !form.stock_symbol.trim()} className="h-9 text-[12px]">保存复盘</Button>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold">
            {(overview?.strategy_avg_return_pct ?? 0) >= 0 ? <TrendingUp className="h-4 w-4 text-green-500" /> : <TrendingDown className="h-4 w-4 text-red-400" />}
            最近复盘
          </div>
          <div className="space-y-2">
            {(data?.recent_reviews || []).map(r => (
              <div key={r.id} className="rounded-md bg-muted/40 p-3 text-[11px]">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{r.stock_market} · {r.stock_symbol}</span>
                  <span className={r.result === 'win' ? 'text-green-500' : r.result === 'loss' ? 'text-red-400' : 'text-muted-foreground'}>{r.result} {r.pnl_pct != null ? signed(r.pnl_pct) : ''}</span>
                </div>
                <div className="mt-1 text-muted-foreground">{r.improvement || r.thesis || '--'}</div>
              </div>
            ))}
            {!data?.recent_reviews?.length && <div className="rounded-md bg-muted/40 p-4 text-[12px] text-muted-foreground">还没有人工复盘。</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
