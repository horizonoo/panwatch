import { useEffect, useState } from 'react'
import { CandlestickChart, Loader2, ShieldAlert, Swords, Target, TrendingUp } from 'lucide-react'
import { recommendationsApi, type TradePlanResponse } from '@panwatch/api'

const pct = (v?: number | null) => v == null ? '--' : `${(v * 100).toFixed(0)}%`
const signedPct = (v?: number | null) => v == null ? '--' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
const price = (v?: number | null) => v == null ? '--' : v.toFixed(v >= 100 ? 2 : 3)

function Stat({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded bg-muted/50 px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`text-[12px] font-semibold tabular-nums ${tone}`}>{value}</div>
    </div>
  )
}

export default function TradePlanPanel({
  symbol,
  market,
}: {
  symbol: string
  market: string
}) {
  const [data, setData] = useState<TradePlanResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    recommendationsApi.getTradePlan({ symbol, market, days: 180, horizon: 3 })
      .then(res => { if (alive) setData(res) })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : '加载失败') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [symbol, market])

  if (loading) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-md bg-muted/40 px-3 py-2 text-[12px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        正在加载交易计划...
      </div>
    )
  }
  if (error) {
    return <div className="mt-3 rounded-md bg-destructive/10 px-3 py-2 text-[12px] text-destructive">{error}</div>
  }
  if (!data?.available) {
    return <div className="mt-3 rounded-md bg-muted/40 px-3 py-2 text-[12px] text-muted-foreground">暂无可用交易计划</div>
  }

  const plan = data.plan
  const op = data.operability
  const debate = data.debate
  const follow = data.paper_follow
  const opt = data.options_advice
  const rr = plan?.risk_reward ? `1:${plan.risk_reward.toFixed(1)}` : '--'

  return (
    <div className="mt-3 space-y-3 rounded-md border border-border/60 bg-background/70 p-3">
      <div className="grid gap-2 md:grid-cols-4">
        <Stat label="入场区间" value={`${price(plan?.entry_low)} ~ ${price(plan?.entry_high)}`} />
        <Stat label="目标价" value={price(plan?.target_price)} tone="text-green-500" />
        <Stat label="止损价" value={price(plan?.stop_loss)} tone="text-red-400" />
        <Stat label="盈亏比" value={rr} />
      </div>

      <div>
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold">
          <Target className="h-3.5 w-3.5 text-primary" />
          历史可操作性
        </div>
        <div className="grid gap-2 md:grid-cols-5">
          <Stat label="策略样本" value={`${op?.strategy_samples ?? 0}次`} />
          <Stat label="策略胜率" value={pct(op?.strategy_win_rate)} />
          <Stat label="目标命中" value={pct(op?.target_hit_rate)} />
          <Stat label="止损触发" value={pct(op?.stop_hit_rate)} />
          <Stat label="平均收益" value={signedPct(op?.avg_return_pct)} tone={(op?.avg_return_pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-400'} />
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-green-500">
            <TrendingUp className="h-3.5 w-3.5" />
            多头理由
          </div>
          <div className="space-y-1 text-[11px] text-muted-foreground">
            {(debate?.bulls || []).slice(0, 4).map((x, i) => <div key={i}>+ {x}</div>)}
            {(!debate?.bulls || debate.bulls.length === 0) && <div>暂无明确多头证据</div>}
          </div>
        </div>
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-amber-500">
            <ShieldAlert className="h-3.5 w-3.5" />
            空头/风险
          </div>
          <div className="space-y-1 text-[11px] text-muted-foreground">
            {(debate?.bears || []).slice(0, 4).map((x, i) => <div key={i}>- {x}</div>)}
            {(!debate?.bears || debate.bears.length === 0) && <div>暂无明确风险提示</div>}
          </div>
        </div>
      </div>

      {opt && (
        <div className="rounded-md border border-primary/20 bg-primary/5 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold">
              <CandlestickChart className="h-3.5 w-3.5 text-primary" />
              股票 + 期权执行建议
            </div>
            <span className="rounded bg-background/70 px-2 py-0.5 text-[10px] text-muted-foreground">
              可信度 {opt.conviction === 'high' ? '高' : opt.conviction === 'medium' ? '中' : '低'}
            </span>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            <div className="rounded bg-background/70 p-2">
              <div className="text-[10px] text-muted-foreground">股票指令</div>
              <div className="mt-1 text-[12px] font-medium">{opt.stock_instruction.text}</div>
              <div className="mt-1 text-[10px] text-muted-foreground">{opt.stock_instruction.stop_rule} · {opt.stock_instruction.target_rule}</div>
            </div>
            <div className="rounded bg-background/70 p-2">
              <div className="text-[10px] text-muted-foreground">期权结构</div>
              <div className="mt-1 text-[12px] font-medium">{opt.option_instruction.name} · {opt.option_instruction.expiry}</div>
              <div className="mt-1 text-[10px] leading-relaxed text-muted-foreground">{opt.option_instruction.instruction}</div>
            </div>
          </div>
          <div className="mt-2 rounded bg-background/70 p-2 text-[10px] leading-relaxed text-muted-foreground">
            配对思路: {opt.hedge_instruction.instruction}
          </div>
          {opt.learning_rules?.length ? (
            <div className="mt-2 rounded bg-amber-500/10 p-2 text-[10px] leading-relaxed text-amber-600">
              学习规则: {opt.learning_rules.map(x => x.title).join('；')}
            </div>
          ) : null}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/50 pt-2 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><Swords className="h-3.5 w-3.5" /> 分歧点: {debate?.key_disagreement || '--'}</span>
        <span>模拟盘: {follow?.open_position ? `持仓中，浮盈亏 ${follow.open_position.unrealized_pnl}` : '未持仓'}</span>
        <span>近 {follow?.recent_closed ?? 0} 笔闭环胜率 {pct(follow?.recent_win_rate)}</span>
      </div>
    </div>
  )
}
