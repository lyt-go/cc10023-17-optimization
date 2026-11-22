import { request } from './client'

export interface Receipt {
  回执编号: string
  result_id: number
  结果编号: string
  分析批次: string
  处理结论: string
  归并编号: string | null
  同洞察来源编号: string[]
  说明: string
  处理时间: string
}

export interface MergedInsight {
  归并编号: string
  分析主题: string
  模型指标口径: string
  关键分群: string[]
  覆盖人群: string[]
  建议动作: string
  置信度: number | null
  来源编号: string[]
  分析批次: string[]
  来源数量: number
  规则编号: string | null
  下发状态: string
}

export interface PublishedRule {
  规则编号: string
  归并编号: string
  分析主题: string
  分群规则: string
  适用人群: string[]
  建议动作: string
  置信度: number | null
  优先级: number | null
  状态: string
  标注编号: string
  来源编号: string[]
  分析批次: string[]
  刷新次数: number
  刷新版本: string
}

export interface DisposalRecord {
  应用编号: string
  处置对象: string
  处置状态: string
  采用动作: string | null
  采用规则: string[]
  冲突动作?: string[]
  建议优先规则?: string
  优先级依据?: string
  暂缓依据?: string
  应用次数: number
  命中规则: PublishedRule[]
}

async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

async function get<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export const insightApi = {
  merge: (resultIds: number[]) =>
    post<{ ok: boolean; 归并组: MergedInsight[]; 独立洞察: MergedInsight[]; 回执: Receipt[] }>(
      '/api/insight/merge', { result_ids: resultIds }),
  merges: () => get<{ items: MergedInsight[] }>('/api/insight/merges'),
  receipts: () => get<{ items: Receipt[] }>('/api/insight/receipts'),
  publish: () => post<{ 新建规则: string[]; 复用规则: string[]; 规则总数: number }>(
    '/api/insight/publish-rules'),
  rules: () => get<{ items: PublishedRule[] }>('/api/insight/rules'),
  apply: (subject: string) =>
    post<{ application: DisposalRecord }>('/api/insight/apply', { subject }),
  refresh: () =>
    post<{ 刷新规则数: number; 规则总数: number; 规则数不变: boolean; 保留的历史标注: string[] }>(
      '/api/insight/refresh-rules'),
  trace: (subject: string) =>
    get<{ 最新处置: DisposalRecord | null; 应用历史: DisposalRecord[] }>(
      `/api/insight/trace/${encodeURIComponent(subject)}`),
}
