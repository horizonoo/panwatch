/**
 * 回测优化面板 — 触发多轮历史回放优化，展示各市场最佳量化策略。
 * 依赖:
 *   POST /api/backtest-optimizer/run
 *   GET  /api/backtest-optimizer/status
 *   GET  /api/backtest-optimizer/latest
 */
import { useEffect, useRef, useState } from 'react'
import { fetchAPI } from '@panwatch/api'
import { Play, RefreshCw, Trophy, TrendingUp, Loader2, Info } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface ParamMetrics {
  trades: number
  win_rate: number
  total_return: number
  annualized_return: number
  sharpe: number
  profit_factor: number
  max_drawdown: number
  expectancy?: number
  avg_holding_bars?: number
  target_hit_rate?: number
  stop_hit_rate?: number
  expire_rate?: number
  score: number
}
interface RankItem {
  strategy_code: string
  strategy_name: string
  score: number
  win_rate: number
  total_return: number
  is_win_rate: number
  trades: number
}
interface MarketBest {
  strategy_code: string
  strategy_name: string
  signal_count: number
  params: { stop_pct: number; target_pct: number; holding_days: number }
  metrics: ParamMetrics
  in_sample: { trades: number; win_rate: number; total_return: number; score: number }
  playbook?: {
    action_label?: string
    entry_rule?: string
    risk_control?: string
    position_sizing?: string
    parameter_quality?: {
      level?: 'high' | 'medium' | 'low'
      label?: string
      reasons?: string[]
    }
    price_formula?: {
      entry?: string
      stop_loss?: string
      target_price?: string
    }
  }
  ranking: RankItem[]
}
interface Report {
  created_at: string
  universe_size: number
  bars_loaded: number
  rounds: number
  per_market_best: Record<string, MarketBest | null>
  elapsed_sec: number
  notes: string
}

const MARKET_LABEL: Record<string, string> = { CN: 'A股', HK: '港股', US: '美股' }
const MARKET_COLOR: Record<string, string> = {
  CN: 'border-red-500/30 bg-red-500/5',
  HK: 'border-blue-500/30 bg-blue-500/5',
  US: 'border-green-500/30 bg-green-500/5',
}

const pct = (v: number) => `${(v * 100).toFixed(1)}%`
const signed = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`
const signedPctValue = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

function MetricCell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-muted-foreground">{label}</span>
      <span className={`text-[13px] font-semibold tabular-nums ${color || ''}`}>{value}</span>
    </div>
  )
}

function MarketCard({ market, best }: { market: string; best: MarketBest | null }) {
  if (!best) {
    return (
      <div className={`rounded-lg border p-4 ${MARKET_COLOR[market] || ''}`}>
        <div className="text-[13px] font-semibold mb-1">{MARKET_LABEL[market] || market}</div>
        <div className="text-[11px] text-muted-foreground">样本不足，暂无可信结果</div>
      </div>
    )
  }
  const m = best.metrics
  const winColor = m.win_rate >= 0.55 ? 'text-green-500' : m.win_rate >= 0.45 ? 'text-yellow-500' : 'text-red-400'
  const retColor = m.total_return >= 0 ? 'text-green-500' : 'text-red-400'
  const quality = best.playbook?.parameter_quality
  const qualityTone = quality?.level === 'high'
    ? 'text-green-500'
    : quality?.level === 'medium'
      ? 'text-amber-500'
      : quality?.level === 'low'
        ? 'text-red-400'
        : 'text-muted-foreground'

  return (
    <div className={`rounded-lg border p-4 space-y-3 ${MARKET_COLOR[market] || ''}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Trophy className="w-4 h-4 text-amber-500" />
          <span className="text-[14px] font-bold">{MARKET_LABEL[market] || market}</span>
        </div>
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
          {best.strategy_name}
        </span>
      </div>

      {/* 核心指标 — 样本外真实战绩 */}
      <div>
        <div className="text-[10px] text-amber-600 font-medium mb-1">样本外真实战绩（未参与调参）</div>
        <div className="grid grid-cols-4 gap-2">
          <MetricCell label="胜率" value={pct(m.win_rate)} color={winColor} />
          <MetricCell label="总收益" value={signed(m.total_return)} color={retColor} />
          <MetricCell label="盈亏比" value={m.profit_factor >= 99 ? '∞' : m.profit_factor.toFixed(2)} />
          <MetricCell label="回撤" value={pct(m.max_drawdown)} color="text-muted-foreground" />
        </div>
      </div>

      <div className="rounded-md border border-border/50 bg-background/60 p-3 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[11px] font-semibold text-foreground">市场级策略模板</div>
          <span className={`text-[10px] font-medium ${qualityTone}`}>
            可信度 {quality?.label || '--'}
          </span>
        </div>
        <div className="text-[11px] leading-relaxed text-muted-foreground">
          {best.playbook?.entry_rule || `${best.strategy_name} 触发后的下一交易日开盘买入。`}
        </div>
        <div className="grid grid-cols-3 gap-2 text-[10px]">
          <div className="rounded bg-muted/60 px-2 py-1.5">
            <div className="text-muted-foreground">止损幅度</div>
            <div className="font-mono text-foreground">{pct(best.params.stop_pct)}</div>
          </div>
          <div className="rounded bg-muted/60 px-2 py-1.5">
            <div className="text-muted-foreground">止盈幅度</div>
            <div className="font-mono text-foreground">{pct(best.params.target_pct)}</div>
          </div>
          <div className="rounded bg-muted/60 px-2 py-1.5">
            <div className="text-muted-foreground">最长持有</div>
            <div className="font-mono text-foreground">{best.params.holding_days} 个交易日</div>
          </div>
        </div>
        <div className="text-[10px] leading-relaxed text-muted-foreground">
          {best.playbook?.risk_control || '跌到止损价卖出，冲到目标价止盈，到期未达标则退出。'}
        </div>
        {quality?.reasons?.length ? (
          <div className="text-[10px] leading-relaxed text-muted-foreground">
            可信度依据: {quality.reasons.join('；')}
          </div>
        ) : null}
      </div>

      <div>
        <div className="text-[10px] text-muted-foreground mb-1">可操作性检验</div>
        <div className="grid grid-cols-4 gap-2">
          <MetricCell label="目标命中" value={pct(m.target_hit_rate ?? 0)} />
          <MetricCell label="止损触发" value={pct(m.stop_hit_rate ?? 0)} />
          <MetricCell label="单笔期望" value={signedPctValue(m.expectancy ?? 0)} color={(m.expectancy ?? 0) >= 0 ? 'text-green-500' : 'text-red-400'} />
          <MetricCell label="平均持有" value={`${(m.avg_holding_bars ?? 0).toFixed(1)}日`} />
        </div>
      </div>

      {/* 样本内对比(过拟合提示) */}
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
        <span>样本内调参时:</span>
        <span>胜率 {pct(best.in_sample.win_rate)}</span>·
        <span>收益 {signed(best.in_sample.total_return)}</span>
        {best.in_sample.win_rate - m.win_rate > 0.15 && (
          <span className="text-orange-500">⚠ 样本外明显回落，注意过拟合</span>
        )}
      </div>

      {/* 最优参数 */}
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground border-t border-border/40 pt-2">
        <TrendingUp className="w-3 h-3" />
        最优出场:
        <span className="text-foreground">止损 {pct(best.params.stop_pct)}</span>·
        <span className="text-foreground">止盈 {pct(best.params.target_pct)}</span>·
        <span className="text-foreground">持有 {best.params.holding_days} 日</span>
        <span className="ml-auto">{m.trades} 笔 · 评分 {m.score.toFixed(3)}</span>
      </div>

      {/* 策略排行 */}
      {best.ranking && best.ranking.length > 1 && (
        <details className="group">
          <summary className="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground list-none flex items-center gap-1">
            <span className="opacity-60 group-open:rotate-90 transition-transform">▶</span>
            全部 {best.ranking.length} 个策略对比
          </summary>
          <table className="w-full mt-2 text-[11px]">
            <thead>
              <tr className="text-muted-foreground text-left">
                <th className="font-normal py-1">策略</th>
                <th className="font-normal text-right">胜率</th>
                <th className="font-normal text-right">收益</th>
                <th className="font-normal text-right">笔数</th>
                <th className="font-normal text-right">评分</th>
              </tr>
            </thead>
            <tbody>
              {best.ranking.map((r, i) => (
                <tr key={r.strategy_code} className={i === 0 ? 'font-medium' : ''}>
                  <td className="py-0.5">{i === 0 ? '🏆 ' : ''}{r.strategy_name}</td>
                  <td className="text-right tabular-nums">{pct(r.win_rate)}</td>
                  <td className={`text-right tabular-nums ${r.total_return >= 0 ? 'text-green-500' : 'text-red-400'}`}>{signed(r.total_return)}</td>
                  <td className="text-right tabular-nums text-muted-foreground">{r.trades}</td>
                  <td className="text-right tabular-nums">{r.score.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-[10px] text-muted-foreground mt-1">胜率/收益均为样本外口径</div>
        </details>
      )}
    </div>
  )
}

export default function BacktestOptimizerPanel() {
  const [report, setReport] = useState<Report | null>(null)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadLatest = async () => {
    try {
      const res = await fetchAPI('/backtest-optimizer/latest') as any
      if (res?.available && res.report) setReport(res.report)
    } catch (e: any) {
      setError(e.message || '加载失败')
    }
  }

  const poll = async () => {
    try {
      const s = await fetchAPI('/backtest-optimizer/status') as any
      setProgress(s?.progress || '')
      if (!s?.running) {
        setRunning(false)
        if (pollRef.current) clearInterval(pollRef.current)
        if (s?.last_error) setError(s.last_error)
        await loadLatest()
      }
    } catch { /* ignore */ }
  }

  const start = async () => {
    setError('')
    setRunning(true)
    setProgress('启动中...')
    try {
      await fetchAPI('/backtest-optimizer/run?rounds=3&max_per_market=40&history_days=750', { method: 'POST' })
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(poll, 3000)
    } catch (e: any) {
      setError(e.message || '启动失败')
      setRunning(false)
    }
  }

  useEffect(() => {
    loadLatest()
    // 进入时若后台正在跑，恢复轮询
    fetchAPI('/backtest-optimizer/status').then((s: any) => {
      if (s?.running) {
        setRunning(true)
        pollRef.current = setInterval(poll, 3000)
      }
    }).catch(() => {})
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] text-muted-foreground flex items-center gap-1.5">
          <Info className="w-3.5 h-3.5" />
          对自选股+推荐股回放约 3 年真实行情，筛选各市场更可操作的策略参数；个股价格看「交易计划」
        </div>
        <Button size="sm" onClick={start} disabled={running} className="h-7 text-[11px] shrink-0">
          {running ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <Play className="w-3 h-3 mr-1" />}
          {running ? '优化中...' : '运行优化'}
        </Button>
      </div>

      {running && (
        <div className="flex items-center gap-2 text-[12px] text-primary bg-primary/5 rounded p-3">
          <Loader2 className="w-4 h-4 animate-spin shrink-0" />
          {progress || '正在加载历史数据并回测，预计数分钟...'}
        </div>
      )}

      {error && (
        <div className="text-[12px] text-destructive bg-destructive/10 rounded p-3">{error}</div>
      )}

      {!report && !running && (
        <div className="card p-6 text-center text-[12px] text-muted-foreground">
          尚无优化结果，点击「运行优化」开始（首次约需 2-5 分钟）
        </div>
      )}

      {report && (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            {['CN', 'HK', 'US'].map(m => (
              <MarketCard key={m} market={m} best={report.per_market_best[m]} />
            ))}
          </div>
          <div className="flex items-center gap-2 text-[11px] text-green-600 bg-green-500/5 rounded px-3 py-2">
            <Trophy className="w-3.5 h-3.5 shrink-0" />
            已生效: 上述市场级止损/止盈/持有天数会作为<b>模拟盘开仓参数</b>参考；具体个股目标价/止损价以交易计划为准
          </div>
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            <RefreshCw className="w-3 h-3" />
            上次运行: {new Date(report.created_at).toLocaleString('zh-CN')} ·
            股票池 {report.universe_size} · 加载 {report.bars_loaded} 只 ·
            {report.rounds} 轮 · 耗时 {report.elapsed_sec}s
          </div>
          <div className="text-[10px] text-muted-foreground/70 leading-relaxed">
            ⚠️ 方法: 前65%历史调参、后35%样本外验证，展示为<b>样本外</b>真实战绩(避免过拟合自夸)。
            这里展示的是市场级参数有效性，不是某只股票的具体买卖价格。
            已扣 A股/港股/美股真实交易成本(佣金+印花税+规费+滑点)。
            综合评分 = 胜率40% + 收益35% + 盈亏比25%。历史表现不代表未来收益，仅供参考。
          </div>
        </>
      )}
    </div>
  )
}
