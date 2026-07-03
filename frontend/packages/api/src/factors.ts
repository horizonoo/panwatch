import { fetchAPI } from './client'

export interface FactorWeight {
  factor_code: string
  market: string
  weight: number
  is_pinned: boolean
  auto_calibrate: boolean
  last_ic: number | null
  last_ir: number | null
  last_sample_size: number | null
  last_calibrated_at: string | null
  reason: string
  updated_at: string | null
}

export interface FactorWeightUpdatePayload {
  weight?: number
  is_pinned?: boolean
  auto_calibrate?: boolean
}

export interface FactorIcEntry {
  ic: number | null
  ir: number | null
  sample_size: number
  ic_periods: number
}

export interface FactorIcResponse {
  horizon: number
  days: number
  market: string | null
  factors: Record<string, FactorIcEntry>
}

export const factorsApi = {
  /** 因子权重列表(每因子按市场区分,含最近 IC/IR 标定结果)。 */
  list: () => fetchAPI<{ items: FactorWeight[] }>('/factors/weights'),

  /** 更新单个因子权重(手动权重 / 锁定 / 自动标定开关)。 */
  update: (factorCode: string, market: string, patch: FactorWeightUpdatePayload) =>
    fetchAPI<FactorWeight>(`/factors/weights/${factorCode}/${market}`, {
      method: 'POST',
      body: JSON.stringify(patch),
    }),

  /** 因子 IC / IR 有效性评估。 */
  getFactorIc: (params?: { days?: number; horizon?: number }) => {
    const q = new URLSearchParams()
    if (params?.days != null) q.set('days', String(params.days))
    if (params?.horizon != null) q.set('horizon', String(params.horizon))
    const qs = q.toString()
    return fetchAPI<FactorIcResponse>(`/recommendations/strategy-factor-ic${qs ? `?${qs}` : ''}`)
  },
}
