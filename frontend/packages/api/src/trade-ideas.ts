import { fetchAPI } from './client'

export interface TradeIdeaLeg {
  symbol: string
  name: string
  market: string
  direction: 'long' | 'short' | string
  instrument: string
  role: string
}

export interface TradeIdea {
  id: number
  title: string
  source: string
  raw_text: string
  thesis: string
  strategy_type: string
  status: 'watching' | 'ready' | 'open' | 'closed' | 'archived' | string
  conviction: string
  event_date: string
  entry_start: string
  entry_end: string
  legs: TradeIdeaLeg[]
  plan: {
    summary?: string
    scorecard?: {
      logic_strength?: number
      catalyst_strength?: number
      data_reliability?: number
      payoff_quality?: number
      discipline_fit?: number
      overall?: number
      grade?: string
      model?: string
      model_label?: string
      weights?: Record<string, number>
      evidence?: Record<string, string[]>
      summary?: string
    }
    reasoning_steps?: string[]
    learning_notes?: string[]
    construction?: string[]
    time_plan?: string[]
    entry_triggers?: string[]
    exit_rules?: string[]
    option_checks?: string[]
    review_template?: string[]
  }
  catalysts: string[]
  risk_checks: string[]
  data_sources: { name: string; use: string }[]
  metrics: Record<string, number | string | null>
  created_at: string
  updated_at: string
}

export interface TradeIdeaScoreModel {
  id: number
  model_key: string
  label: string
  weights: Record<string, number>
  enabled: boolean
  created_at: string
  updated_at: string
}

export const tradeIdeasApi = {
  list: (status = 'active', limit = 30) =>
    fetchAPI<{ items: TradeIdea[] }>(`/trade-ideas?status=${encodeURIComponent(status)}&limit=${encodeURIComponent(String(limit))}`),

  create: (payload: { raw_text: string; title?: string; source?: string }) =>
    fetchAPI<TradeIdea>('/trade-ideas', {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: 30000,
    }),

  updateStatus: (id: number, status: TradeIdea['status']) =>
    fetchAPI<TradeIdea>(`/trade-ideas/${encodeURIComponent(String(id))}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    }),

  listScoreModels: () =>
    fetchAPI<{ items: TradeIdeaScoreModel[] }>('/trade-ideas/score-models'),

  updateScoreModel: (modelKey: string, payload: { label: string; weights: Record<string, number>; enabled?: boolean }) =>
    fetchAPI<TradeIdeaScoreModel>(`/trade-ideas/score-models/${encodeURIComponent(modelKey)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
}
