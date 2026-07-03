import { fetchAPI } from './client'

export interface LearningOverview {
  paper_trades: number
  paper_win_rate: number
  paper_avg_return_pct: number
  real_sells: number
  real_win_rate: number
  real_avg_return_pct: number
  strategy_outcomes: number
  strategy_win_rate: number
  strategy_avg_return_pct: number
  manual_reviews: number
}

export interface LearningStrategyRow {
  strategy_code: string
  market: string
  samples: number
  win_rate: number
  avg_return_pct: number
  target_hit_rate: number
  stop_hit_rate: number
}

export interface LearningRule {
  id?: number
  scope_type: string
  scope_key: string
  stock_market: string
  strategy_code: string
  severity: 'info' | 'warn' | 'block' | string
  title: string
  recommendation: string
  evidence: Record<string, any>
  status?: string
  generated_at?: string
}

export interface TradeReview {
  id: number
  source: string
  source_id?: number | null
  stock_symbol: string
  stock_market: string
  stock_name: string
  strategy_code: string
  strategy_name: string
  action_taken: string
  thesis: string
  result: string
  pnl_pct?: number | null
  mistake_tags: string[]
  improvement: string
  confidence_before?: number | null
  confidence_after?: number | null
  created_at: string
}

export interface LearningSummary {
  window_days: number
  overview: LearningOverview
  strategies: LearningStrategyRow[]
  weak_strategies: LearningStrategyRow[]
  strong_strategies: LearningStrategyRow[]
  review_tags: { tag: string; count: number }[]
  recent_reviews: TradeReview[]
  active_rules: LearningRule[]
  candidate_rules: LearningRule[]
}

export const learningApi = {
  summary: (days = 90) => fetchAPI<LearningSummary>(`/learning/summary?days=${encodeURIComponent(String(days))}`),
  rebuildRules: (days = 90) => fetchAPI<{ count: number; items: LearningRule[] }>(
    `/learning/rules/rebuild?days=${encodeURIComponent(String(days))}`,
    { method: 'POST' }
  ),
  listReviews: (limit = 50) => fetchAPI<{ items: TradeReview[] }>(`/learning/reviews?limit=${encodeURIComponent(String(limit))}`),
  createReview: (body: Partial<TradeReview> & { stock_symbol: string }) => fetchAPI<TradeReview>('/learning/reviews', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  listRules: (status: 'active' | 'archived' | 'all' = 'active') =>
    fetchAPI<{ items: LearningRule[] }>(`/learning/rules?status=${encodeURIComponent(status)}`),
}
