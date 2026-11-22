<template>
  <section class="insight-page">
    <h2 class="page-title">洞察归并工作台</h2>
    <p class="page-desc">
      同主题挖掘结果按「模型指标口径 + 关键分群 + 覆盖人群 + 建议动作」识别等价复跑，
      覆盖或动作不同保持独立，避免同主题不同人群的数据互相覆盖。
    </p>

    <div class="panel">
      <h3>1. 选择同主题挖掘结果并批量归并</h3>
      <div class="result-picker">
        <label v-for="row in miningRows" :key="row.id" class="picker-item">
          <input v-model="selectedIds" type="checkbox" :value="Number(row.id)" />
          <span>{{ row['结果编号'] }}｜{{ row['分析主题'] }}｜覆盖 {{ row['覆盖人群'] }}
            ｜{{ row['建议动作'] }}｜置信度 {{ row['置信度'] }}</span>
        </label>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!selectedIds.length" @click="runMerge">
          批量归并（{{ selectedIds.length }}）
        </button>
        <button class="btn" type="button" @click="loadAll">刷新归并与回执</button>
      </div>
    </div>

    <div v-if="lastMerge" class="panel">
      <h3>2. 本次处理回执（每份来源一条）</h3>
      <table class="data-table">
        <thead>
          <tr><th>回执编号</th><th>来源</th><th>分析批次</th><th>处理结论</th><th>归并编号</th><th>同洞察来源</th></tr>
        </thead>
        <tbody>
          <tr v-for="receipt in lastMerge.回执" :key="receipt.回执编号">
            <td>{{ receipt.回执编号 }}</td>
            <td>{{ receipt.结果编号 }}</td>
            <td>{{ receipt.分析批次 }}</td>
            <td :class="receipt['处理结论'] === '保持独立' ? 'tag warn' : 'tag ok'">
              {{ receipt.处理结论 }}
            </td>
            <td>{{ receipt.归并编号 }}</td>
            <td>{{ receipt.同洞察来源编号.join('、') || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>3. 归并洞察（可按人群应用）</h3>
      <table class="data-table">
        <thead>
          <tr><th>归并编号</th><th>主题/口径</th><th>分群</th><th>覆盖人群</th><th>建议动作</th>
            <th>置信度</th><th>来源编号/批次</th><th>下发状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in merges" :key="item.归并编号">
            <td>{{ item.归并编号 }}</td>
            <td>{{ item.分析主题 }}<br /><small>{{ item.模型指标口径 }}</small></td>
            <td>{{ item.关键分群.join('、') }}</td>
            <td>{{ item.覆盖人群.join('、') }}</td>
            <td>{{ item.建议动作 }}</td>
            <td>{{ item.置信度 ?? '—' }}</td>
            <td>{{ item.来源编号.join('、') }}<br /><small>{{ item.分析批次.join('、') }}</small></td>
            <td>{{ item.下发状态 }}</td>
          </tr>
        </tbody>
      </table>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="runPublish">下发分群规则（幂等）</button>
        <button class="btn" type="button" @click="runRefresh">刷新已发布规则</button>
      </div>
      <p v-if="publishMsg" class="msg">{{ publishMsg }}</p>
    </div>

    <div class="panel">
      <h3>4. 已发布规则与按人群应用</h3>
      <table class="data-table">
        <thead>
          <tr><th>规则编号</th><th>分群规则</th><th>适用人群</th><th>动作</th><th>置信度</th>
            <th>优先级</th><th>出处批次</th><th>版本</th></tr>
        </thead>
        <tbody>
          <tr v-for="rule in rules" :key="rule.规则编号">
            <td>{{ rule.规则编号 }}</td>
            <td>{{ rule.分群规则 }}</td>
            <td>{{ rule.适用人群.join('、') }}</td>
            <td>{{ rule.建议动作 }}</td>
            <td>{{ rule.置信度 ?? '—' }}</td>
            <td>P{{ rule.优先级 }}</td>
            <td><small>{{ rule.分析批次.join('、') }}</small></td>
            <td>{{ rule.刷新版本 }}（{{ rule.刷新次数 }}）</td>
          </tr>
        </tbody>
      </table>
      <div class="apply-bar">
        <input v-model="subject" placeholder="输入处置对象，如 甲 / 乙 / 丙" />
        <button class="btn primary" type="button" :disabled="!subject.trim()" @click="runApply">应用规则</button>
        <button class="btn" type="button" :disabled="!subject.trim()" @click="runTrace">追溯处置</button>
      </div>
      <div v-if="application" class="result-card" :class="cardClass(application.处置状态)">
        <strong>{{ application.处置对象 }}：{{ application.处置状态 }}</strong>
        <p v-if="application.采用动作">采用动作：{{ application.采用动作 }}（{{ application.采用规则.join('、') }}）</p>
        <p v-if="application.暂缓依据">暂缓依据：{{ application.暂缓依据 }}</p>
        <p v-if="application.优先级依据">优先级依据：{{ application.优先级依据 }}</p>
        <ul>
          <li v-for="rule in application.命中规则" :key="rule.规则编号">
            {{ rule.规则编号 }} P{{ rule.优先级 }} → {{ rule.建议动作 }}
            （来源 {{ rule.来源编号.join('、') }} / {{ rule.分析批次.join('、') }}）
          </li>
        </ul>
      </div>
      <div v-if="trace && trace.最新处置" class="result-card">
        <strong>追溯：{{ trace.最新处置.处置对象 }} 共 {{ trace.应用历史.length }} 次应用记录</strong>
        <p>最新状态：{{ trace.最新处置.处置状态 }}，动作：{{ trace.最新处置.采用动作 ?? '未执行' }}</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listEntries } from '@/api/backend-service'
import { insightApi, type DisposalRecord, type MergedInsight, type PublishedRule, type Receipt } from '@/api/insight'

interface MiningRow {
  id: number
  [key: string]: string | number
}

const miningRows = ref<MiningRow[]>([])
const selectedIds = ref<number[]>([])
const merges = ref<MergedInsight[]>([])
const rules = ref<PublishedRule[]>([])
const receipts = ref<Receipt[]>([])
const lastMerge = ref<{ 回执: Receipt[] } | null>(null)
const application = ref<DisposalRecord | null>(null)
const trace = ref<{ 最新处置: DisposalRecord | null; 应用历史: DisposalRecord[] } | null>(null)
const subject = ref('')
const publishMsg = ref('')

function cardClass(status: string): string {
  if (status.includes('冲突')) return 'conflict'
  if (status === '已执行') return 'executed'
  return ''
}

async function loadMiningRows() {
  const page = await listEntries('mining_result', { size: '200' })
  miningRows.value = page.items.filter((row) => row['分析批次']) as MiningRow[]
}

async function loadAll() {
  const [mergePage, rulePage, receiptPage] = await Promise.all([
    insightApi.merges(), insightApi.rules(), insightApi.receipts()])
  merges.value = mergePage.items
  rules.value = rulePage.items
  receipts.value = receiptPage.items
}

async function runMerge() {
  const result = await insightApi.merge(selectedIds.value)
  lastMerge.value = { 回执: result.回执 }
  publishMsg.value = ''
  await loadAll()
}

async function runPublish() {
  const result = await insightApi.publish()
  publishMsg.value = `新建 ${result.新建规则.length} 条，复用 ${result.复用规则.length} 条，规则总数 ${result.规则总数}`
  await loadAll()
}

async function runRefresh() {
  const result = await insightApi.refresh()
  publishMsg.value = `刷新 ${result.刷新规则数} 条，规则数不变：${result.规则数不变}，历史标注 ${result.保留的历史标注.length} 条原样保留`
  await loadAll()
}

async function runApply() {
  const result = await insightApi.apply(subject.value.trim())
  application.value = result.application
  trace.value = null
}

async function runTrace() {
  trace.value = await insightApi.trace(subject.value.trim())
}

onMounted(async () => {
  await loadMiningRows()
  await loadAll()
})
</script>

<style scoped>
.insight-page { display: flex; flex-direction: column; gap: 16px; }
.panel { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; }
.result-picker { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; }
.picker-item { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 12px 0; }
.data-table th, .data-table td { border: 1px solid #eef0f3; padding: 8px; text-align: left; vertical-align: top; }
.data-table th { background: #f8fafc; }
.tag { padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.tag.ok { background: #dcfce7; color: #166534; }
.tag.warn { background: #fef9c3; color: #854d0e; }
.apply-bar { display: flex; gap: 8px; align-items: center; }
.apply-bar input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 8px; min-width: 240px; }
.result-card { margin-top: 12px; padding: 12px; border-radius: 8px; background: #f8fafc; border: 1px solid #e5e7eb; }
.result-card.conflict { background: #fef2f2; border-color: #fecaca; }
.result-card.executed { background: #f0fdf4; border-color: #bbf7d0; }
.msg { color: #2563eb; font-size: 13px; }
small { color: #6b7280; }
</style>
