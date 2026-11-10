<script setup>
import { ref, onMounted, nextTick, onBeforeUnmount } from "vue";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { api, run, statuses, date } from "../api";
echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);
const props = defineProps({ mode: String }),
  loading = ref(false),
  metrics = ref(null),
  policies = ref([]),
  alerts = ref([]),
  modelReports = ref(null),
  health = ref(null),
  chartElement = ref(null),
  page = ref(1),
  edit = ref(false);
const priority = ref(2),
  response = ref(14400),
  resolution = ref(172800),
  warning = ref(1800),
  reason = ref("");
let chart,
  observer,
  loadSequence = 0,
  disposed = false;
const percent = (value) =>
  value?.rate == null
    ? "暂无可计算样本"
    : `${(value.rate * 100).toFixed(1)}%（${value.numerator}/${value.denominator}）`;
async function load() {
  if (disposed) return;
  const token = ++loadSequence;
  loading.value = true;
  await run(async () => {
    if (props.mode === "sla") {
      const [p, a] = await Promise.all([
        api.get("/admin/policies"),
        api.get("/admin/alerts", { params: { page: page.value } }),
      ]);
      if (disposed || token !== loadSequence) return;
      policies.value = p.data;
      alerts.value = a.data;
    } else {
      const [m, report, state] = await Promise.all([
        api.get("/metrics"),
        api.get("/admin/model-reports"),
        api.get("/admin/operations/health").catch(() => ({ data: null })),
      ]);
      if (disposed || token !== loadSequence) return;
      metrics.value = m.data;
      modelReports.value = report.data;
      health.value = state.data;
      await nextTick();
      if (disposed || token !== loadSequence || !chartElement.value) return;
      chart ||= echarts.init(chartElement.value);
      const items = Object.entries(metrics.value.by_status);
      chart.setOption({
        color: ["#087f8c"],
        tooltip: {},
        grid: { left: 100, right: 30, top: 15, bottom: 25 },
        xAxis: { type: "value", minInterval: 1 },
        yAxis: {
          type: "category",
          data: items.map(([name]) => statuses[name]),
        },
        series: [
          {
            type: "bar",
            data: items.map(([, count]) => count),
            barMaxWidth: 24,
          },
        ],
      });
      observer ||= new ResizeObserver(() => chart?.resize());
      observer.observe(chartElement.value);
    }
  });
  if (token === loadSequence) loading.value = false;
}
async function scan() {
  await run(() => api.post("/admin/sla-scan"));
  await load();
}
async function acknowledge(row) {
  await run(() => api.post(`/admin/alerts/${row.id}/acknowledge`));
  await load();
}
async function escalate(row) {
  await run(() =>
    api.patch(`/tickets/${row.ticket_id}`, {
      expected_version: row.ticket_version,
      priority: 0,
      reason: "管理员根据SLA告警人工升级为P0",
    }),
  );
  await load();
}
async function save() {
  await run(async () => {
    await api.post("/admin/policies", {
      priority: priority.value,
      response_seconds: response.value,
      resolution_seconds: resolution.value,
      warning_seconds: warning.value,
      reason: reason.value,
    });
    edit.value = false;
    await load();
  });
}
onMounted(load);
onBeforeUnmount(() => {
  disposed = true;
  ++loadSequence;
  observer?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>{{ mode === "sla" ? "SLA 管理" : "数据统计" }}</h1>
      <p>
        {{
          mode === "sla"
            ? "自然小时计时，提醒与升级留痕"
            : "从实际记录计算，区分演示与生产效果"
        }}
      </p>
    </div>
    <div class="inline">
      <el-button @click="load">刷新</el-button
      ><el-button v-if="mode === 'sla'" @click="scan">立即扫描</el-button
      ><el-button v-if="mode === 'sla'" type="primary" @click="edit = true"
        >新建 SLA 配置版本</el-button
      >
    </div>
  </div>
  <div v-loading="loading">
    <template v-if="mode === 'sla'"
      ><section class="panel padded">
        <h2>当前有效政策</h2>
        <p class="muted small">
          政策变更影响新周期；已有周期保留时限快照。升级优先级可收紧时限，降级不会延长。
        </p>
        <el-table :data="policies.filter((p) => p.active)"
          ><el-table-column label="优先级"
            ><template #default="{ row }"
              >P{{ row.priority }}</template
            ></el-table-column
          ><el-table-column prop="version" label="版本" /><el-table-column
            label="首次响应（小时）"
            ><template #default="{ row }">{{
              row.response_seconds / 3600
            }}</template></el-table-column
          ><el-table-column label="解决时限（小时）"
            ><template #default="{ row }">{{
              row.resolution_seconds / 3600
            }}</template></el-table-column
          ><el-table-column label="提前提醒（分钟）"
            ><template #default="{ row }">{{
              row.warning_seconds / 60
            }}</template></el-table-column
          ></el-table
        >
      </section>
      <section class="panel padded space-top">
        <h2>超时与临期提醒</h2>
        <p class="muted small">
          后台每30秒扫描；提醒在系统内持久化，不代表已发送邮件或短信。
        </p>
        <el-table :data="alerts" empty-text="暂无SLA提醒"
          ><el-table-column
            prop="ticket_no"
            label="工单"
            min-width="190"
          /><el-table-column
            prop="kind"
            label="提醒"
            min-width="190"
          /><el-table-column label="截止时间" min-width="180"
            ><template #default="{ row }">{{
              date(row.due_at)
            }}</template></el-table-column
          ><el-table-column label="操作" min-width="180"
            ><template #default="{ row }"
              ><el-button
                text
                :disabled="!!row.acknowledged_at"
                @click="acknowledge(row)"
                >{{ row.acknowledged_at ? "已确认" : "确认接手" }}</el-button
              ><el-button text @click="escalate(row)"
                >人工升级 P0</el-button
              ></template
            ></el-table-column
          ></el-table
        >
        <div class="inline space-top">
          <el-button
            :disabled="page === 1"
            @click="
              page--;
              load();
            "
            >上一页</el-button
          ><span>第 {{ page }} 页</span
          ><el-button
            :disabled="alerts.length < 50"
            @click="
              page++;
              load();
            "
            >下一页</el-button
          >
        </div>
      </section>
    </template>
    <template v-else-if="metrics"
      ><el-alert
        title="以下为本地模拟业务记录；没有生产效果结论"
        type="info"
        :closable="false"
      />
      <section
        v-if="health"
        class="panel padded space-top"
        aria-label="运行状态"
      >
        <h2>运行状态</h2>
        <p class="small muted">
          最近检查：{{
            date(health.generated_at)
          }}。点击刷新重新读取，不触发模型调用。
        </p>
        <el-table :data="health.components">
          <el-table-column label="服务" width="130"
            ><template #default="{ row }">{{
              {
                database: "业务库",
                worker: "后台任务",
                retrieval: "检索服务",
                index: "知识索引",
              }[row.name]
            }}</template></el-table-column
          >
          <el-table-column label="状态" width="100"
            ><template #default="{ row }">{{
              { healthy: "正常", degraded: "需处理", unknown: "未确认" }[
                row.status
              ]
            }}</template></el-table-column
          >
          <el-table-column prop="detail" label="检查结果" min-width="210" />
        </el-table>
        <p>
          等待任务 {{ health.queue.queued }} · 运行中
          {{ health.queue.running }} · 失败 {{ health.queue.failed }} · 过期租约
          {{ health.queue.expired_leases }}
        </p>
        <p v-if="health.llm_budget.enabled">
          百炼调用：已预留 {{ health.llm_budget.reserved }} /
          {{ health.llm_budget.total }}，剩余
          {{ health.llm_budget.remaining }} 次。
        </p>
        <p
          v-for="notice in health.alerts"
          :key="notice"
          class="operation-notice"
        >
          {{ notice }}
        </p>
        <p class="small muted">
          当前API进程共处理 {{ health.http.requests }} 次请求，服务端错误
          {{ health.http.server_errors }} 次。最近
          {{ health.http.latency_samples }} 次请求 P95：{{
            health.http.p95_ms == null
              ? "暂无样本"
              : `${health.http.p95_ms.toFixed(1)} ms`
          }}；进程重启后重新计数，含本机接口耗时，不代表百炼推理延迟。
        </p>
      </section>
      <section class="panel padded space-top">
        <el-alert
          v-if="!health"
          title="运行状态暂不可读取，请刷新或检查服务日志"
          type="warning"
          :closable="false"
          class="space-top"
        />
        <h2>工单状态分布 · 共 {{ metrics.tickets }} 条</h2>
        <div
          ref="chartElement"
          class="chart"
          role="img"
          aria-label="工单状态数量柱状图"
        ></div>
        <el-descriptions :column="2" border
          ><el-descriptions-item label="首次响应 SLA">{{
            percent(metrics.sla_response)
          }}</el-descriptions-item
          ><el-descriptions-item label="解决 SLA">{{
            percent(metrics.sla_resolution)
          }}</el-descriptions-item
          ><el-descriptions-item label="草稿采纳率">{{
            percent(metrics.draft_acceptance)
          }}</el-descriptions-item
          ><el-descriptions-item label="LLM 草稿采纳率">{{
            percent(metrics.ai_acceptance)
          }}</el-descriptions-item
          ><el-descriptions-item label="重开率">{{
            percent(metrics.reopen_rate)
          }}</el-descriptions-item
          ><el-descriptions-item label="平均首次响应">{{
            metrics.average_first_response_seconds == null
              ? "暂无样本"
              : `${metrics.average_first_response_seconds.toFixed(1)} 秒`
          }}</el-descriptions-item
          ><el-descriptions-item label="平均处理时长">{{
            metrics.average_resolution_seconds == null
              ? "暂无样本"
              : `${metrics.average_resolution_seconds.toFixed(1)} 秒`
          }}</el-descriptions-item></el-descriptions
        >
        <p class="small muted">
          SLA
          分母为已完成相应动作或已到期的周期，尚未到期的待处理周期不计入。派单正确率缺少独立复核真值，暂不计算。
        </p>
      </section>
      <section class="panel padded space-top">
        <h2>
          分类训练实验（{{
            modelReports?.classification?.purpose === "formal"
              ? "AI复核离线评估"
              : "smoke流程验证"
          }}）
        </h2>
        <p class="small muted">
          {{ modelReports?.classification?.data_note || "尚无实验数据说明" }}
        </p>
        <el-table
          :data="modelReports?.classification?.models || []"
          empty-text="尚无分类实验产物"
        >
          <el-table-column prop="model" label="模型" min-width="130" />
          <el-table-column prop="sample_count" label="测试样本" />
          <el-table-column label="Accuracy"
            ><template #default="{ row }">{{
              row.accuracy.toFixed(3)
            }}</template></el-table-column
          >
          <el-table-column label="Precision"
            ><template #default="{ row }">{{
              row.precision.toFixed(3)
            }}</template></el-table-column
          >
          <el-table-column label="Recall"
            ><template #default="{ row }">{{
              row.recall.toFixed(3)
            }}</template></el-table-column
          >
          <el-table-column label="Macro-F1"
            ><template #default="{ row }">{{
              row.macro_f1.toFixed(3)
            }}</template></el-table-column
          >
        </el-table>
        <el-collapse
          ><el-collapse-item
            v-for="model in modelReports?.classification?.models || []"
            :key="model.model"
            :title="`${model.model} 混淆矩阵（行=真实类别，列=预测类别）`"
            ><table class="matrix">
              <thead>
                <tr>
                  <th>真实 / 预测</th>
                  <th
                    v-for="label in [
                      '订单',
                      '支付',
                      '物流',
                      '退款',
                      '质量',
                      '账户',
                    ]"
                    :key="label"
                  >
                    {{ label }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, index) in model.confusion_matrix" :key="index">
                  <th>
                    {{
                      ["订单", "支付", "物流", "退款", "质量", "账户"][index]
                    }}
                  </th>
                  <td v-for="(count, col) in row" :key="col">{{ count }}</td>
                </tr>
              </tbody>
            </table></el-collapse-item
          ></el-collapse
        >
      </section>
      <section class="panel padded space-top">
        <h2>检索实验对照</h2>
        <p class="small muted">
          {{ modelReports?.retrieval?.dataset || "尚无检索评测产物" }}
        </p>
        <el-table
          :data="modelReports?.retrieval?.strategies || []"
          empty-text="尚未运行评测"
          ><el-table-column prop="strategy" label="策略" /><el-table-column
            prop="recall_at_5"
            label="Recall@5" /><el-table-column
            prop="mrr_at_5"
            label="MRR@5" /><el-table-column
            prop="p50_ms"
            label="P50 ms" /><el-table-column prop="p95_ms" label="P95 ms"
        /></el-table>
        <p class="small muted">
          正式分类训练数据未完成。RF / fastText / BERT
          结果仅验证训练流程，禁止作为上线质量承诺。
        </p>
      </section>
      <section class="panel padded space-top">
        <h2>大模型调用记录</h2>
        <el-table :data="metrics.llm_invocations" empty-text="尚无付费调用"
          ><el-table-column label="用途"
            ><template #default="{ row }">{{
              row.purpose === "DATA_REVIEW" ? "AI数据复核" : "客服草稿"
            }}</template></el-table-column
          ><el-table-column prop="ticket_id" label="工单ID" /><el-table-column
            prop="model"
            label="模型"
          /><el-table-column prop="status" label="状态" /><el-table-column
            label="Token"
            ><template #default="{ row }">{{
              row.usage?.total_tokens ?? "未知"
            }}</template></el-table-column
          ><el-table-column label="成本"
            ><template #default
              >未配置核验价格，暂不计算</template
            ></el-table-column
          ></el-table
        >
      </section>
    </template>
  </div>
  <el-dialog v-model="edit" title="新建 SLA 配置版本" width="min(520px, 94vw)"
    ><el-form label-position="top" @submit.prevent="save"
      ><el-form-item label="优先级"
        ><el-select v-model="priority"
          ><el-option
            v-for="i in 4"
            :key="i"
            :label="`P${i - 1}`"
            :value="i - 1" /></el-select></el-form-item
      ><el-form-item label="首次响应秒数"
        ><el-input-number v-model="response" :min="60" /></el-form-item
      ><el-form-item label="解决时限秒数"
        ><el-input-number v-model="resolution" :min="response" /></el-form-item
      ><el-form-item label="提前提醒秒数"
        ><el-input-number v-model="warning" :min="0" /></el-form-item
      ><el-form-item label="变更原因"
        ><el-input v-model="reason" /></el-form-item
      ><el-button type="primary" native-type="submit"
        >保存新版本</el-button
      ></el-form
    ></el-dialog
  >
</template>
<style scoped>
.operation-notice {
  color: #925922;
  overflow-wrap: anywhere;
}
</style>
