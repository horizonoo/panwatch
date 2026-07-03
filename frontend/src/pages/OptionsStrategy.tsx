import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CandlestickChart, CheckCircle2, ClipboardList, Database, Loader2, RefreshCw, Shield, Swords, Target, TrendingDown, TrendingUp } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { recommendationsApi, tradeIdeasApi, type OptionsAdviceResponse, type TradeIdea, type TradeIdeaScoreModel } from '@panwatch/api'

const markets = [
  { value: 'US', label: '美股' },
  { value: 'HK', label: '港股' },
  { value: 'CN', label: 'A股' },
]

const price = (v?: number | null) => v == null ? '--' : v.toFixed(Math.abs(v) >= 100 ? 2 : 3)
const pct = (v?: number | null) => v == null ? '--' : `${v.toFixed(1)}%`
const numOrUndefined = (v: string) => {
  const n = Number(v)
  return Number.isFinite(n) ? n : undefined
}

const scoreLabels: Record<string, string> = {
  logic_strength: '逻辑',
  catalyst_strength: '催化',
  data_reliability: '数据',
  payoff_quality: '赔率',
  discipline_fit: '纪律',
}

function ToneBadge({ value }: { value?: string }) {
  const tone = value === 'high'
    ? 'border-green-500/30 bg-green-500/10 text-green-500'
    : value === 'medium'
      ? 'border-amber-500/30 bg-amber-500/10 text-amber-500'
      : 'border-red-500/30 bg-red-500/10 text-red-400'
  return <span className={`rounded border px-2 py-0.5 text-[11px] ${tone}`}>可信度 {value === 'high' ? '高' : value === 'medium' ? '中' : '低'}</span>
}

function Stat({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md bg-muted/50 px-3 py-2">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`mt-1 text-[13px] font-semibold tabular-nums ${tone}`}>{value}</div>
    </div>
  )
}

function IdeaPill({ children, tone = '' }: { children: ReactNode; tone?: string }) {
  return <span className={`rounded border border-border bg-muted/40 px-2 py-1 text-[11px] ${tone}`}>{children}</span>
}

function ScoreCell({ label, value, evidence }: { label: string; value?: number; evidence?: string[] }) {
  const n = typeof value === 'number' ? value : 0
  const tone = n >= 80 ? 'text-green-500' : n >= 65 ? 'text-amber-500' : 'text-red-400'
  return (
    <div className="rounded bg-muted/40 p-2">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`mt-1 text-[13px] font-semibold tabular-nums ${tone}`}>{n ? n.toFixed(0) : '--'}</div>
      {evidence?.length ? (
        <div className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">{evidence[0]}</div>
      ) : null}
    </div>
  )
}

export default function OptionsStrategyPage() {
  const [symbol, setSymbol] = useState('MU')
  const [market, setMarket] = useState('US')
  const [currentPrice, setCurrentPrice] = useState('')
  const [ivRank, setIvRank] = useState('')
  const [holdingQty, setHoldingQty] = useState('')
  const [riskBudget, setRiskBudget] = useState('2')
  const [data, setData] = useState<OptionsAdviceResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [ideaText, setIdeaText] = useState('')
  const [ideas, setIdeas] = useState<TradeIdea[]>([])
  const [selectedIdea, setSelectedIdea] = useState<TradeIdea | null>(null)
  const [ideasLoading, setIdeasLoading] = useState(false)
  const [savingIdea, setSavingIdea] = useState(false)
  const [scoreModels, setScoreModels] = useState<TradeIdeaScoreModel[]>([])
  const [scoreModelsOpen, setScoreModelsOpen] = useState(false)
  const [savingModel, setSavingModel] = useState<string | null>(null)

  const advice = data?.advice
  const plan = data?.plan
  const op = data?.operability
  const hasResult = Boolean(data)

  const loadIdeas = async () => {
    setIdeasLoading(true)
    try {
      const res = await tradeIdeasApi.list('active', 30)
      setIdeas(res.items || [])
      if (!selectedIdea && res.items?.length) setSelectedIdea(res.items[0])
    } catch (e) {
      console.error(e)
    } finally {
      setIdeasLoading(false)
    }
  }

  useEffect(() => { loadIdeas() }, [])

  const loadScoreModels = async () => {
    try {
      const res = await tradeIdeasApi.listScoreModels()
      setScoreModels(res.items || [])
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => { loadScoreModels() }, [])

  const saveIdea = async () => {
    const raw = ideaText.trim()
    if (!raw) {
      setError('请先粘贴交易思路原文')
      return
    }
    setSavingIdea(true)
    setError('')
    try {
      const created = await tradeIdeasApi.create({ raw_text: raw, source: 'manual' })
      setSelectedIdea(created)
      setIdeas(prev => [created, ...prev.filter(x => x.id !== created.id)])
      setIdeaText('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存交易思路失败')
    } finally {
      setSavingIdea(false)
    }
  }

  const updateIdeaStatus = async (status: TradeIdea['status']) => {
    if (!selectedIdea) return
    const updated = await tradeIdeasApi.updateStatus(selectedIdea.id, status)
    setSelectedIdea(updated)
    setIdeas(prev => prev.map(x => x.id === updated.id ? updated : x).filter(x => x.status !== 'archived'))
  }

  const setScoreModelWeight = (modelKey: string, factor: string, value: string) => {
    const n = Number(value)
    setScoreModels(prev => prev.map(model => model.model_key === modelKey
      ? { ...model, weights: { ...model.weights, [factor]: Number.isFinite(n) ? n / 100 : 0 } }
      : model
    ))
  }

  const saveScoreModel = async (model: TradeIdeaScoreModel) => {
    setSavingModel(model.model_key)
    try {
      const updated = await tradeIdeasApi.updateScoreModel(model.model_key, {
        label: model.label,
        weights: model.weights,
        enabled: model.enabled,
      })
      setScoreModels(prev => prev.map(x => x.model_key === updated.model_key ? updated : x))
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存评分模型失败')
    } finally {
      setSavingModel(null)
    }
  }

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await recommendationsApi.getOptionsAdvice({
        symbol: symbol.trim().toUpperCase(),
        market,
        current_price: numOrUndefined(currentPrice),
        iv_rank: numOrUndefined(ivRank),
        holding_qty: Math.max(0, Math.floor(numOrUndefined(holdingQty) || 0)),
        risk_budget_pct: numOrUndefined(riskBudget),
        horizon: 10,
        days: 180,
      })
      setData(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  const primaryIcon = useMemo(() => {
    if (!advice) return <CandlestickChart className="h-4 w-4 text-primary" />
    if (advice.stance === 'bearish') return <TrendingDown className="h-4 w-4 text-red-400" />
    if (advice.stance.includes('bullish')) return <TrendingUp className="h-4 w-4 text-green-500" />
    return <CandlestickChart className="h-4 w-4 text-primary" />
  }, [advice])

  return (
    <div className="page-container pb-10">
      <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-[20px] font-bold tracking-tight md:text-[22px]">期权执行台</h1>
          <p className="mt-1 text-[12px] text-muted-foreground">按个股交易计划生成股票指令、期权结构和对冲思路；真实合约筛选需要补充期权链。</p>
        </div>
        <Button onClick={load} disabled={loading || !symbol.trim()} className="h-9 text-[12px]">
          {loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
          生成建议
        </Button>
      </div>

      <section className="mb-4 rounded-lg border border-border bg-card p-4">
        <button
          type="button"
          onClick={() => setScoreModelsOpen(v => !v)}
          className="flex w-full items-center justify-between text-left"
        >
          <div>
            <h2 className="text-[14px] font-semibold">评分模型配置</h2>
            <p className="mt-1 text-[11px] text-muted-foreground">调整美股、港股、A股、跨市场配对的评分权重。保存后影响新生成的交易思路。</p>
          </div>
          <span className="text-[12px] text-primary">{scoreModelsOpen ? '收起' : '展开'}</span>
        </button>
        {scoreModelsOpen && (
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {scoreModels.map(model => (
              <div key={model.model_key} className="rounded-md border border-border bg-muted/20 p-3">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div>
                    <div className="text-[12px] font-semibold">{model.label}</div>
                    <div className="text-[10px] text-muted-foreground">{model.model_key}</div>
                  </div>
                  <Button onClick={() => saveScoreModel(model)} disabled={savingModel === model.model_key} className="h-7 px-2 text-[11px]">
                    {savingModel === model.model_key ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                    保存
                  </Button>
                </div>
                <div className="grid gap-2">
                  {Object.entries(scoreLabels).map(([key, label]) => {
                    const value = Math.round(((model.weights?.[key] ?? 0) * 100))
                    return (
                      <label key={key} className="grid grid-cols-[52px_1fr_44px] items-center gap-2 text-[11px]">
                        <span className="text-muted-foreground">{label}</span>
                        <input
                          type="range"
                          min={0}
                          max={60}
                          step={1}
                          value={value}
                          onChange={e => setScoreModelWeight(model.model_key, key, e.target.value)}
                        />
                        <span className="text-right tabular-nums">{value}%</span>
                      </label>
                    )
                  })}
                </div>
                <div className="mt-2 text-[10px] text-muted-foreground">保存时会自动归一化到 100%。</div>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
        <section className="rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ClipboardList className="h-4 w-4 text-primary" />
              <div>
                <h2 className="text-[14px] font-semibold">交易思路收件箱</h2>
                <p className="text-[11px] text-muted-foreground">粘贴长文后自动生成配对/期权计划、时间点、建仓触发和风险检查。</p>
              </div>
            </div>
            <Button onClick={saveIdea} disabled={savingIdea || !ideaText.trim()} className="h-8 text-[12px]">
              {savingIdea ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />}
              记录并生成
            </Button>
          </div>
          <textarea
            value={ideaText}
            onChange={e => setIdeaText(e.target.value)}
            placeholder="粘贴类似“做多海力士 + 做空美光”的交易思路。系统会提取标的、催化日期、相对强弱触发、期权结构、退出条件和推荐数据源。"
            className="min-h-[180px] w-full resize-y rounded-md border border-border bg-background p-3 text-[12px] leading-relaxed outline-none focus:border-primary/40"
          />
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            <div className="rounded bg-muted/40 p-3 text-[11px] text-muted-foreground">1. 原文归档: 保留假设、数字和来源。</div>
            <div className="rounded bg-muted/40 p-3 text-[11px] text-muted-foreground">2. 结构化: 拆成 long/short 腿、日期和指标。</div>
            <div className="rounded bg-muted/40 p-3 text-[11px] text-muted-foreground">3. 执行前检查: 财报口径、流动性、期权链、相对强弱。</div>
          </div>
        </section>

        <section className="rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Database className="h-4 w-4 text-primary" />
              <h2 className="text-[14px] font-semibold">已记录思路</h2>
            </div>
            <Button variant="ghost" onClick={loadIdeas} disabled={ideasLoading} className="h-8 text-[12px]">
              {ideasLoading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
              刷新
            </Button>
          </div>
          <div className="max-h-[270px] space-y-2 overflow-y-auto pr-1">
            {!ideas.length && <div className="rounded border border-dashed border-border p-6 text-center text-[12px] text-muted-foreground">暂无记录。下一条交易灵感，就让它有地方安家。</div>}
            {ideas.map(idea => (
              <button
                key={idea.id}
                onClick={() => setSelectedIdea(idea)}
                className={`w-full rounded-md border p-3 text-left transition-colors ${selectedIdea?.id === idea.id ? 'border-primary/40 bg-primary/5' : 'border-border bg-muted/20 hover:bg-muted/40'}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="text-[12px] font-semibold">{idea.title}</div>
                  <span className="rounded bg-background px-2 py-0.5 text-[10px] text-muted-foreground">{idea.status}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {idea.legs?.map(leg => (
                    <IdeaPill key={`${idea.id}-${leg.direction}-${leg.symbol}`} tone={leg.direction === 'long' ? 'text-green-500' : 'text-red-400'}>
                      {leg.direction === 'long' ? '多' : '空'} {leg.symbol}
                    </IdeaPill>
                  ))}
                  {idea.entry_start && <IdeaPill>建仓 {idea.entry_start}</IdeaPill>}
                  {idea.event_date && <IdeaPill>催化 {idea.event_date}</IdeaPill>}
                </div>
              </button>
            ))}
          </div>
        </section>
      </div>

      {selectedIdea && (
        <div className="mb-4 rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <div className="text-[15px] font-bold">{selectedIdea.title}</div>
              <p className="mt-1 max-w-4xl text-[12px] leading-relaxed text-muted-foreground">{selectedIdea.thesis}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {(['watching', 'ready', 'open', 'closed', 'archived'] as const).map(s => (
                <Button key={s} variant={selectedIdea.status === s ? 'default' : 'ghost'} onClick={() => updateIdeaStatus(s)} className="h-7 px-2 text-[11px]">
                  {s}
                </Button>
              ))}
            </div>
          </div>
          <div className="mb-3 grid gap-3 lg:grid-cols-[280px_1fr_1fr]">
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="text-[12px] font-semibold">系统评分</div>
                <span className="rounded bg-background px-2 py-0.5 text-[11px] font-semibold">
                  {selectedIdea.plan?.scorecard?.grade || '--'} · {selectedIdea.plan?.scorecard?.overall ?? '--'}
                </span>
              </div>
              {selectedIdea.plan?.scorecard?.model_label && (
                <div className="mb-2 rounded bg-background/70 px-2 py-1 text-[10px] text-muted-foreground">
                  {selectedIdea.plan.scorecard.model_label}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <ScoreCell label="逻辑" value={selectedIdea.plan?.scorecard?.logic_strength} evidence={selectedIdea.plan?.scorecard?.evidence?.logic_strength} />
                <ScoreCell label="催化" value={selectedIdea.plan?.scorecard?.catalyst_strength} evidence={selectedIdea.plan?.scorecard?.evidence?.catalyst_strength} />
                <ScoreCell label="数据" value={selectedIdea.plan?.scorecard?.data_reliability} evidence={selectedIdea.plan?.scorecard?.evidence?.data_reliability} />
                <ScoreCell label="赔率" value={selectedIdea.plan?.scorecard?.payoff_quality} evidence={selectedIdea.plan?.scorecard?.evidence?.payoff_quality} />
                <ScoreCell label="纪律" value={selectedIdea.plan?.scorecard?.discipline_fit} evidence={selectedIdea.plan?.scorecard?.evidence?.discipline_fit} />
              </div>
              {selectedIdea.plan?.scorecard?.summary && (
                <div className="mt-2 rounded bg-background/70 p-2 text-[11px] leading-relaxed text-muted-foreground">
                  {selectedIdea.plan.scorecard.summary}
                </div>
              )}
            </div>
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">论证过程</div>
              <div className="space-y-1.5">
                {(selectedIdea.plan?.reasoning_steps || []).map((x, idx) => (
                  <div key={x} className="rounded bg-background/70 p-2 text-[11px] leading-relaxed text-muted-foreground">
                    {idx + 1}. {x}
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">学习笔记</div>
              <div className="space-y-1.5">
                {(selectedIdea.plan?.learning_notes || []).map(x => (
                  <div key={x} className="rounded bg-background/70 p-2 text-[11px] leading-relaxed text-muted-foreground">{x}</div>
                ))}
              </div>
            </div>
          </div>
          <div className="grid gap-3 lg:grid-cols-3">
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">交易腿</div>
              <div className="space-y-2">
                {selectedIdea.legs?.map(leg => (
                  <div key={`${leg.direction}-${leg.symbol}`} className="rounded bg-background/70 p-2 text-[11px]">
                    <div className={`font-semibold ${leg.direction === 'long' ? 'text-green-500' : 'text-red-400'}`}>{leg.direction.toUpperCase()} · {leg.name}</div>
                    <div className="mt-1 text-muted-foreground">{leg.instrument} · {leg.role}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">时间点</div>
              <div className="space-y-1.5">
                {(selectedIdea.plan?.time_plan || []).map(x => <div key={x} className="rounded bg-background/70 p-2 text-[11px] text-muted-foreground">{x}</div>)}
              </div>
            </div>
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">建仓触发</div>
              <div className="space-y-1.5">
                {(selectedIdea.plan?.entry_triggers || []).map(x => <div key={x} className="rounded bg-background/70 p-2 text-[11px] text-muted-foreground">{x}</div>)}
              </div>
            </div>
          </div>
          <div className="mt-3 grid gap-3 lg:grid-cols-3">
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">期权表达</div>
              <div className="space-y-1.5">
                {(selectedIdea.plan?.construction || []).map(x => <div key={x} className="rounded bg-background/70 p-2 text-[11px] text-muted-foreground">{x}</div>)}
                {(selectedIdea.plan?.option_checks || []).map(x => <div key={x} className="rounded bg-background/70 p-2 text-[11px] text-muted-foreground">{x}</div>)}
              </div>
            </div>
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">退出与风控</div>
              <div className="space-y-1.5">
                {(selectedIdea.plan?.exit_rules || []).map(x => <div key={x} className="rounded bg-background/70 p-2 text-[11px] text-muted-foreground">{x}</div>)}
                {selectedIdea.risk_checks?.map(x => <div key={x} className="rounded bg-amber-500/10 p-2 text-[11px] text-amber-600">{x}</div>)}
              </div>
            </div>
            <div className="rounded-md bg-muted/30 p-3">
              <div className="mb-2 text-[12px] font-semibold">更好的信息源</div>
              <div className="space-y-1.5">
                {selectedIdea.data_sources?.map(x => (
                  <div key={x.name} className="rounded bg-background/70 p-2 text-[11px]">
                    <div className="font-semibold">{x.name}</div>
                    <div className="mt-1 text-muted-foreground">{x.use}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-3 rounded-md bg-muted/30 p-3">
            <div className="mb-2 text-[12px] font-semibold">复盘问题</div>
            <div className="grid gap-2 md:grid-cols-2">
              {(selectedIdea.plan?.review_template || []).map(x => (
                <div key={x} className="rounded bg-background/70 p-2 text-[11px] leading-relaxed text-muted-foreground">{x}</div>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="mb-4 grid gap-3 rounded-lg border border-border bg-card p-4 md:grid-cols-6">
        <label className="space-y-1 text-[12px]">
          <span className="text-muted-foreground">标的</span>
          <input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} className="h-9 w-full rounded-md border border-border bg-background px-3 text-[13px]" />
        </label>
        <label className="space-y-1 text-[12px]">
          <span className="text-muted-foreground">市场</span>
          <Select value={market} onValueChange={setMarket}>
            <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
            <SelectContent>{markets.map(x => <SelectItem key={x.value} value={x.value}>{x.label}</SelectItem>)}</SelectContent>
          </Select>
        </label>
        <label className="space-y-1 text-[12px]">
          <span className="text-muted-foreground">当前价</span>
          <input value={currentPrice} onChange={e => setCurrentPrice(e.target.value)} placeholder="可空" className="h-9 w-full rounded-md border border-border bg-background px-3 text-[13px]" />
        </label>
        <label className="space-y-1 text-[12px]">
          <span className="text-muted-foreground">IV Rank</span>
          <input value={ivRank} onChange={e => setIvRank(e.target.value)} placeholder="0-100" className="h-9 w-full rounded-md border border-border bg-background px-3 text-[13px]" />
        </label>
        <label className="space-y-1 text-[12px]">
          <span className="text-muted-foreground">持仓股数</span>
          <input value={holdingQty} onChange={e => setHoldingQty(e.target.value)} placeholder="0" className="h-9 w-full rounded-md border border-border bg-background px-3 text-[13px]" />
        </label>
        <label className="space-y-1 text-[12px]">
          <span className="text-muted-foreground">单笔风险%</span>
          <input value={riskBudget} onChange={e => setRiskBudget(e.target.value)} className="h-9 w-full rounded-md border border-border bg-background px-3 text-[13px]" />
        </label>
      </div>

      {error && <div className="mb-4 rounded-md bg-destructive/10 p-3 text-[12px] text-destructive">{error}</div>}

      {!hasResult && !loading && (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-[12px] text-muted-foreground">
          输入标的后点击「生成建议」。如果 PanWatch 没有该标的交易计划，页面会告诉你需要补哪些数据。
        </div>
      )}

      {data && !data.available && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4">
          <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-amber-600">
            <AlertTriangle className="h-4 w-4" />
            暂无可执行交易计划
          </div>
          <p className="text-[12px] text-muted-foreground">{data.message}</p>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {(data.data_needed || []).map(x => (
              <div key={x} className="rounded bg-background/70 px-3 py-2 text-[11px] text-muted-foreground">{x}</div>
            ))}
          </div>
        </div>
      )}

      {data?.available && advice && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                {primaryIcon}
                <div>
                  <div className="text-[15px] font-bold">{data.symbol} · {data.signal?.stock_name || data.symbol}</div>
                  <div className="text-[11px] text-muted-foreground">{data.signal?.strategy_name || '--'} · {data.signal?.action_label || '--'}</div>
                </div>
              </div>
              <ToneBadge value={advice.conviction} />
            </div>
            <div className="grid gap-2 md:grid-cols-6">
              <Stat label="当前价" value={price(advice.price_snapshot.current_price)} />
              <Stat label="入场区间" value={`${price(plan?.entry_low)} ~ ${price(plan?.entry_high)}`} />
              <Stat label="止损" value={price(plan?.stop_loss)} tone="text-red-400" />
              <Stat label="目标" value={price(plan?.target_price)} tone="text-green-500" />
              <Stat label="上行空间" value={pct(advice.price_snapshot.upside_pct)} tone="text-green-500" />
              <Stat label="下行风险" value={pct(advice.price_snapshot.downside_pct)} tone="text-red-400" />
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold">
                <Target className="h-4 w-4 text-primary" />
                股票操作
              </div>
              <div className="rounded bg-muted/40 p-3 text-[13px] font-medium">{advice.stock_instruction.text}</div>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                <div className="rounded bg-muted/40 p-2 text-[11px] text-muted-foreground">{advice.stock_instruction.stop_rule}</div>
                <div className="rounded bg-muted/40 p-2 text-[11px] text-muted-foreground">{advice.stock_instruction.target_rule}</div>
              </div>
              <div className="mt-2 text-[11px] text-muted-foreground">单笔最大风险预算: {advice.stock_instruction.risk_budget_pct}%</div>
            </div>

            <div className="rounded-lg border border-border bg-card p-4">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold">
                <CandlestickChart className="h-4 w-4 text-primary" />
                期权结构
              </div>
              <div className="rounded bg-muted/40 p-3">
                <div className="text-[13px] font-semibold">{advice.option_instruction.name}</div>
                <div className="mt-1 text-[11px] text-muted-foreground">方向: {advice.option_instruction.direction} · 到期: {advice.option_instruction.expiry}</div>
                <div className="mt-2 text-[12px] leading-relaxed text-muted-foreground">{advice.option_instruction.instruction}</div>
              </div>
              <div className="mt-2 text-[11px] text-muted-foreground">{advice.option_instruction.use_when}</div>
            </div>
          </div>

          {advice.learning_rules?.length ? (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-amber-600">
                <AlertTriangle className="h-4 w-4" />
                学习规则已介入
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {advice.learning_rules.map((rule, idx) => (
                  <div key={`${rule.title}-${idx}`} className="rounded bg-background/70 p-3 text-[11px]">
                    <div className="font-semibold">{rule.severity === 'block' ? '拦截' : rule.severity === 'warn' ? '警告' : '提示'} · {rule.title}</div>
                    <div className="mt-1 leading-relaxed text-muted-foreground">{rule.recommendation}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="rounded-lg border border-border bg-card p-4">
            <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold">
              <Swords className="h-4 w-4 text-primary" />
              配对/对冲交易
            </div>
            <div className="rounded bg-muted/40 p-3 text-[12px] leading-relaxed text-muted-foreground">{advice.hedge_instruction.instruction}</div>
            <div className="mt-2 text-[11px] text-muted-foreground">{advice.hedge_instruction.use_when}</div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold">
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                执行检查
              </div>
              <div className="space-y-1.5">
                {advice.checklist.map(x => <div key={x} className="rounded bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">{x}</div>)}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold">
                <Shield className="h-4 w-4 text-amber-500" />
                需要补充的期权链字段
              </div>
              <div className="space-y-1.5">
                {advice.data_needed.map(x => <div key={x} className="rounded bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">{x}</div>)}
              </div>
            </div>
          </div>

          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-[12px] text-amber-600">
            历史样本 {op?.strategy_samples ?? 0} 次，策略胜率 {op ? `${(op.strategy_win_rate * 100).toFixed(1)}%` : '--'}，平均收益 {op ? `${op.avg_return_pct.toFixed(2)}%` : '--'}。期权建议不自动下单，真实合约必须通过流动性和最大亏损检查。
          </div>
        </div>
      )}
    </div>
  )
}
