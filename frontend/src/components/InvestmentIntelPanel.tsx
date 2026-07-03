/**
 * 投资情报面板 — 展示多源聚合信号 + 置信度评分
 * 依赖 GET /api/history?agent_name=investment_intel
 */
import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'
import { AlertCircle, ExternalLink, RefreshCw, Shield, TrendingDown, TrendingUp } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface IntelSignal {
  title: string
  source: string
  direction: 'bullish' | 'bearish' | 'neutral'
  confidence: number
  weight: number
  evidence: string
}

interface ConfidenceBreakdown {
  source_diversity: number
  signal_agreement: number
  data_freshness: number
  signal_volume: number
}

interface IntelStructured {
  symbol: string
  name: string
  overall_confidence: number
  sentiment: 'bullish' | 'bearish' | 'neutral'
  sentiment_score: number
  signals: IntelSignal[]
  confidence_breakdown: ConfidenceBreakdown
  recommendation: string
  recommendation_label: string
}

interface HistoryItem {
  id: number
  created_at: string
  title: string
  content: string
  raw_data?: {
    intel_structured?: IntelStructured
    stock_count?: number
    timestamp?: string
  }
}

const DIRECTION_ICON = {
  bullish: <TrendingUp className="w-3 h-3 text-green-500" />,
  bearish: <TrendingDown className="w-3 h-3 text-red-500" />,
  neutral: <span className="w-3 h-3 text-muted-foreground inline-block">→</span>,
}

const SENTIMENT_COLOR = {
  bullish: 'text-green-500',
  bearish: 'text-red-500',
  neutral: 'text-muted-foreground',
}

const SENTIMENT_LABEL = {
  bullish: '看涨',
  bearish: '看跌',
  neutral: '中性',
}

const RECOMMENDATION_COLOR: Record<string, string> = {
  buy: 'bg-green-500/10 text-green-600 border-green-500/20',
  hold: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  watch: 'bg-yellow-500/10 text-yellow-600 border-yellow-500/20',
  sell: 'bg-red-500/10 text-red-600 border-red-500/20',
  avoid: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
}

function ConfidenceBar({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-muted-foreground w-16 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-[10px] text-muted-foreground w-8 text-right">{value}%</span>
    </div>
  )
}

function ConfidenceGauge({ value }: { value: number }) {
  const color =
    value >= 70 ? 'text-green-500' :
    value >= 45 ? 'text-yellow-500' :
    'text-red-400'
  const label =
    value >= 70 ? '高' :
    value >= 45 ? '中' :
    '低'

  return (
    <div className="flex flex-col items-center gap-0.5">
      <div className={`text-2xl font-bold tabular-nums ${color}`}>{value}%</div>
      <div className={`text-[10px] font-medium ${color}`}>置信度 · {label}</div>
    </div>
  )
}

function IntelCard({ item }: { item: HistoryItem }) {
  const intel = item.raw_data?.intel_structured
  const [expanded, setExpanded] = useState(false)

  if (!intel) {
    return (
      <div className="card p-4 text-[12px] text-muted-foreground">
        <div className="font-medium mb-1">{item.title}</div>
        <div className="whitespace-pre-wrap text-[11px] leading-relaxed line-clamp-4">{item.content}</div>
      </div>
    )
  }

  const breakdown = intel.confidence_breakdown || {}

  return (
    <div className="card p-4 space-y-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[13px] font-semibold">
            {intel.name}
            <span className="text-muted-foreground font-normal ml-1">({intel.symbol})</span>
          </div>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            {new Date(item.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <ConfidenceGauge value={intel.overall_confidence} />
          <div className={`text-[11px] font-medium px-2 py-0.5 rounded border ${RECOMMENDATION_COLOR[intel.recommendation] || RECOMMENDATION_COLOR.watch}`}>
            {intel.recommendation_label}
          </div>
        </div>
      </div>

      {/* Sentiment */}
      <div className="flex items-center gap-4 text-[11px]">
        <span className="text-muted-foreground">市场情绪:</span>
        <span className={`font-medium ${SENTIMENT_COLOR[intel.sentiment]}`}>
          {intel.sentiment === 'bullish' ? '📈' : intel.sentiment === 'bearish' ? '📉' : '➡️'}
          {' '}{SENTIMENT_LABEL[intel.sentiment]}
          {intel.sentiment_score !== 0 && <span className="opacity-60 ml-1">({intel.sentiment_score > 0 ? '+' : ''}{intel.sentiment_score})</span>}
        </span>
      </div>

      {/* Signals */}
      {intel.signals && intel.signals.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-medium text-muted-foreground">关键信号</div>
          {intel.signals.slice(0, expanded ? 999 : 3).map((sig, i) => (
            <div key={i} className="flex items-start gap-2 text-[11px]">
              <div className="mt-0.5 shrink-0">{DIRECTION_ICON[sig.direction]}</div>
              <div className="flex-1 min-w-0">
                <span className="font-medium">{sig.title}</span>
                {sig.evidence && <span className="text-muted-foreground ml-1">— {sig.evidence}</span>}
              </div>
              <div className="shrink-0 flex items-center gap-1">
                <span className={`text-[10px] px-1 rounded ${sig.source === 'twitter' ? 'bg-sky-500/10 text-sky-600' : sig.source === 'reddit' ? 'bg-orange-500/10 text-orange-600' : 'bg-purple-500/10 text-purple-600'}`}>
                  {sig.source}
                </span>
                <span className="text-[10px] text-muted-foreground">{sig.confidence}%</span>
              </div>
            </div>
          ))}
          {intel.signals.length > 3 && (
            <button className="text-[11px] text-primary" onClick={() => setExpanded(!expanded)}>
              {expanded ? '收起' : `展开全部 ${intel.signals.length} 条`}
            </button>
          )}
        </div>
      )}

      {/* Confidence Breakdown */}
      <div className="space-y-1 pt-1 border-t border-border/50">
        <div className="flex items-center gap-1 text-[11px] text-muted-foreground mb-1.5">
          <Shield className="w-3 h-3" /> 置信度构成
        </div>
        <ConfidenceBar value={breakdown.signal_agreement ?? 0} label="信号一致性" color="bg-green-500" />
        <ConfidenceBar value={breakdown.data_freshness ?? 0} label="数据时效性" color="bg-blue-500" />
        <ConfidenceBar value={breakdown.source_diversity ?? 0} label="来源多样性" color="bg-purple-500" />
        <ConfidenceBar value={breakdown.signal_volume ?? 0} label="信号数量" color="bg-orange-500" />
      </div>
    </div>
  )
}

export default function InvestmentIntelPanel() {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetchAPI('/history?agent_name=investment_intel&limit=20') as any
      setItems(((res?.items || res?.data || []) as HistoryItem[]).slice(0, 10))
    } catch (e: any) {
      setError(e.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-[12px] text-muted-foreground">
          通过 AgentKey 聚合 Twitter、Reddit、Yahoo Finance 多源数据，评估每只股票的情报置信度
        </div>
        <Button size="sm" variant="ghost" onClick={load} disabled={loading} className="h-7 text-[11px]">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-[12px] text-destructive bg-destructive/10 rounded p-3">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {!loading && items.length === 0 && !error && (
        <div className="card p-6 text-center space-y-2">
          <div className="text-[13px] font-medium">尚无投资情报数据</div>
          <div className="text-[11px] text-muted-foreground">
            需先在设置页配置 <code className="bg-muted px-1 rounded">agentkey_api_key</code>，
            然后在自选股中添加 <strong>investment_intel</strong> Agent 并触发一次运行
          </div>
          <a
            href="https://agentkey.app"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
          >
            获取 AgentKey API Key <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      )}

      <div className="space-y-3">
        {items.map((item) => (
          <IntelCard key={item.id} item={item} />
        ))}
      </div>
    </div>
  )
}
