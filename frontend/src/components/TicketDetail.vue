<script setup>
import { computed, ref, watch } from "vue";
import { Close, Cpu } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { api, run, session, statuses, labels, key, date } from "../api";
const props = defineProps({ ticketId: String }),
  emit = defineEmits(["updated", "close"]);
const ticket = ref(null),
  messages = ref([]),
  events = ref([]),
  sla = ref(null),
  tab = ref("messages"),
  reply = ref(""),
  visibility = ref("PUBLIC"),
  busy = ref(false),
  drafts = ref([]),
  analysis = ref(null),
  business = ref(null),
  businessError = ref("");
const category = ref(0),
  priority = ref(2),
  target = ref(""),
  reason = ref(""),
  editText = ref(""),
  reviewReason = ref("");
const staff = computed(() => session.user.role !== "CUSTOMER"),
  admin = computed(() => session.user.role === "ADMIN");
const activeDraft = computed(() => drafts.value[0]);
const nextStates = computed(() => {
  const state = ticket.value?.status;
  if (["RESOLVED", "CLOSED"].includes(state))
    return session.user.role === "AGENT"
      ? []
      : state === "RESOLVED"
        ? ["CLOSED", "PENDING_ASSIGNMENT"]
        : ["PENDING_ASSIGNMENT"];
  if (!staff.value) return [];
  const next =
    state === "PROCESSING"
      ? ["WAITING_CUSTOMER", "WAITING_EXTERNAL", "RESOLVED"]
      : ["WAITING_CUSTOMER", "WAITING_EXTERNAL"].includes(state)
        ? ["PROCESSING"]
        : [];
  return admin.value && next.length ? [...next, "PENDING_ASSIGNMENT"] : next;
});
let sequence = 0;
const draftStatus = {
  PENDING_REVIEW: "待人工审核",
  ACCEPTED: "已采纳",
  EDITED: "已修改采纳",
  REJECTED: "已拒绝",
  SENT: "已发送",
};
async function load() {
  const requestedId = props.ticketId,
    currentSequence = ++sequence;
  await run(async () => {
    const [t, m, e, s] = await Promise.all([
      api.get(`/tickets/${requestedId}`),
      api.get(`/tickets/${requestedId}/messages`, {
        params: { limit: 100 },
      }),
      api.get(`/tickets/${requestedId}/events`, { params: { limit: 100 } }),
      api.get(`/tickets/${requestedId}/sla`),
    ]);
    if (currentSequence !== sequence || requestedId !== props.ticketId) return;
    ticket.value = t.data;
    target.value = "";
    messages.value = m.data.items;
    events.value = e.data;
    sla.value = s.data;
    priority.value = ticket.value.priority;
    category.value = ticket.value.category ?? 0;
    if (staff.value) {
      const [d, a] = await Promise.all([
        api.get(`/tickets/${requestedId}/assistant/drafts`),
        api.get(`/tickets/${requestedId}/analysis`),
      ]);
      if (currentSequence !== sequence || requestedId !== props.ticketId)
        return;
      drafts.value = d.data;
      analysis.value = a.data;
      editText.value =
        activeDraft.value?.final_body || activeDraft.value?.body || "";
    }
  });
}
watch(
  () => props.ticketId,
  () => {
    ticket.value = null;
    drafts.value = [];
    messages.value = [];
    events.value = [];
    sla.value = null;
    analysis.value = null;
    editText.value = "";
    reviewReason.value = "";
    visibility.value = "PUBLIC";
    businessError.value = "";
    tab.value = "messages";
    business.value = null;
    reply.value = "";
    load();
  },
  { immediate: true },
);
async function mutate(action) {
  busy.value = true;
  await run(async () => {
    await action();
    await load();
    emit("updated");
  });
  busy.value = false;
}
async function sendMessage() {
  if (!reply.value.trim()) return;
  await mutate(async () => {
    await api.post(
      `/tickets/${props.ticketId}/messages`,
      {
        expected_version: ticket.value.version,
        body: reply.value,
        visibility: visibility.value,
      },
      { headers: { "Idempotency-Key": key() } },
    );
    reply.value = "";
  });
}
function classify() {
  if (!reason.value) return ElMessage.warning("请填写操作原因");
  mutate(() =>
    api.post(`/tickets/${props.ticketId}/classification-review`, {
      expected_version: ticket.value.version,
      category: category.value,
      reason: reason.value,
    }),
  );
}
function assign() {
  if (!reason.value) return ElMessage.warning("请填写操作原因");
  mutate(async () => {
    const r = await api.post(`/tickets/${props.ticketId}/assign`, {
      expected_version: ticket.value.version,
      reason: reason.value,
    });
    if (!r.data.assigned)
      ElMessage.warning(`仍在等待队列：${r.data.queue_reason}`);
  });
}
function transition() {
  if (!target.value || !reason.value)
    return ElMessage.warning("请选择状态并填写原因");
  mutate(() =>
    api.post(`/tickets/${props.ticketId}/transitions`, {
      expected_version: ticket.value.version,
      target_status: target.value,
      reason: reason.value,
    }),
  );
}
function setPriority() {
  if (!reason.value) return ElMessage.warning("请填写原因");
  mutate(() =>
    api.patch(`/tickets/${props.ticketId}`, {
      expected_version: ticket.value.version,
      priority: priority.value,
      reason: reason.value,
    }),
  );
}
async function getBusiness() {
  businessError.value = "";
  business.value = null;
  try {
    business.value = (
      await api.get(`/tickets/${props.ticketId}/business-context`)
    ).data;
  } catch (error) {
    businessError.value =
      error.response?.data?.error?.message || "业务记录暂不可用";
  }
}
function generate(mode) {
  if (busy.value) return;
  mutate(() =>
    api.post(`/tickets/${props.ticketId}/assistant/draft`, {
      expected_version: ticket.value.version,
      mode,
    }),
  );
}
function feedback(decision) {
  if (!reviewReason.value.trim())
    return ElMessage.warning("请填写人工审核原因");
  mutate(() =>
    api.post(`/assistant/${activeDraft.value.id}/feedback`, {
      expected_version: activeDraft.value.version,
      decision,
      ...(decision === "EDITED" ? { final_body: editText.value } : {}),
      reason: reviewReason.value,
    }),
  );
}
function sendDraft() {
  mutate(() =>
    api.post(`/assistant/${activeDraft.value.id}/send`, {
      expected_version: activeDraft.value.version,
      ticket_version: ticket.value.version,
    }),
  );
}
</script>
<template>
  <section v-if="ticket" class="detail-column" v-loading="busy">
    <div class="panel detail-main">
      <div class="detail-heading">
        <div>
          <h2>{{ ticket.subject }}</h2>
          <span class="muted small"
            >{{ ticket.ticket_no }} · {{ statuses[ticket.status] }}</span
          >
        </div>
        <el-button
          text
          :icon="Close"
          aria-label="关闭详情"
          @click="emit('close')"
        />
      </div>
      <el-tabs v-model="tab">
        <el-tab-pane label="消息" name="messages"
          ><div class="timeline">
            <article
              v-for="message in messages"
              :key="message.id"
              class="message"
            >
              <div class="message-meta">
                <strong>{{
                  message.author_id === ticket.customer_id ? "客户" : "客服"
                }}</strong
                ><span>{{ date(message.created_at) }}</span
                ><el-tag
                  v-if="message.visibility === 'INTERNAL'"
                  size="small"
                  type="warning"
                  >内部备注</el-tag
                >
              </div>
              <p
                :class="{
                  'staff-message': message.author_id !== ticket.customer_id,
                }"
              >
                {{ message.body }}
              </p>
            </article>
          </div>
          <p class="muted small" v-if="messages.length === 100">
            当前展示最早100条消息；完整记录可通过分页接口查询。
          </p></el-tab-pane
        >
        <el-tab-pane v-if="staff" label="AI 助手" name="ai"
          ><el-alert
            title="分类与模型部署状态"
            :description="
              ticket.analysis_status === 'REVIEWED_MANUALLY'
                ? '已人工分类'
                : '当前分类模型未获正式部署批准，低置信度或不可用任务转人工。'
            "
            type="info"
            :closable="false"
          />
          <p>人工分类：{{ labels[ticket.category] || "待确认" }}</p>
          <p class="small muted">
            订单号候选仅校验格式，存在性和归属仍由业务查询确认。
          </p>
          <ul v-if="analysis?.result?.order_candidates">
            <li
              v-for="item in analysis.result.order_candidates"
              :key="item.start"
            >
              {{ item.value }}（未确认归属）
            </li>
          </ul></el-tab-pane
        >
        <el-tab-pane label="业务记录" name="business"
          ><el-button @click="getBusiness">查询授权业务记录</el-button
          ><el-alert
            v-if="businessError"
            :title="businessError"
            type="warning"
            :closable="false"
            class="space-top"
          /><template v-if="business"
            ><p class="small muted">
              来源：模拟业务数据库 · {{ date(business.queried_at) }}
            </p>
            <el-descriptions :column="1" border
              ><el-descriptions-item label="订单号">{{
                business.order.order_no
              }}</el-descriptions-item
              ><el-descriptions-item label="状态">{{
                business.order.status
              }}</el-descriptions-item
              ><el-descriptions-item label="订单金额"
                >¥{{ business.order.total_amount }}</el-descriptions-item
              ></el-descriptions
            >
            <h3>商品</h3>
            <p v-for="(item, i) in business.items" :key="i">
              {{ item.product_name }} × {{ item.quantity }}
            </p>
            <h3>支付 / 物流 / 退款</h3>
            <p v-for="(item, i) in business.payments" :key="`p${i}`">
              支付 {{ item.status }} · ¥{{ item.amount }}
            </p>
            <p v-for="(item, i) in business.shipments" :key="`s${i}`">
              {{ item.carrier }} · {{ item.status }}
            </p>
            <p v-for="(item, i) in business.refunds" :key="`r${i}`">
              退款 {{ item.status }} · ¥{{ item.amount }}
            </p></template
          ></el-tab-pane
        >
        <el-tab-pane label="操作日志" name="events"
          ><el-timeline
            ><el-timeline-item
              v-for="item in events"
              :key="item.id"
              :timestamp="date(item.created_at)"
              ><strong>{{ item.event_type }}</strong>
              <p>{{ item.reason }}</p></el-timeline-item
            ></el-timeline
          ></el-tab-pane
        >
      </el-tabs>
      <div class="composer">
        <label for="reply-body">{{
          visibility === "INTERNAL" ? "内部备注" : "回复客户"
        }}</label
        ><el-input
          id="reply-body"
          v-model="reply"
          type="textarea"
          :rows="3"
          placeholder="请输入回复内容…"
          maxlength="4000"
        />
        <div class="composer-actions">
          <el-checkbox
            v-if="staff"
            :model-value="visibility === 'INTERNAL'"
            @change="(value) => (visibility = value ? 'INTERNAL' : 'PUBLIC')"
            >仅内部可见</el-checkbox
          ><el-button
            type="primary"
            :disabled="
              !reply.trim() || ['RESOLVED', 'CLOSED'].includes(ticket.status)
            "
            @click="sendMessage"
            >发送回复</el-button
          >
        </div>
      </div>
    </div>
    <div v-if="staff" class="panel ai-panel">
      <div class="section-title">
        <h2>AI 助手</h2>
        <span class="review-badge">{{
          draftStatus[activeDraft?.status] || "待人工审核"
        }}</span>
      </div>
      <p class="muted small">
        {{
          session.capabilities?.llm === "enabled"
            ? "百炼模型仅选择原文证据，发送前必须审核"
            : "LLM 未启用 · 可生成非大模型的证据摘录"
        }}
      </p>
      <div class="inline">
        <el-button :loading="busy" @click="generate('evidence')"
          >生成证据摘录</el-button
        ><el-button
          v-if="session.capabilities?.llm === 'enabled'"
          :loading="busy"
          @click="generate('llm')"
          >生成百炼草稿</el-button
        >
      </div>
      <template v-if="activeDraft"
        ><h3>知识依据</h3>
        <el-collapse
          ><el-collapse-item
            v-for="item in activeDraft.evidence"
            :key="item.chunk_id"
            :title="`${item.title} v${item.revision} [${item.chunk_id}]`"
            ><p class="pre-wrap">{{ item.text }}</p>
            <p class="small muted">{{ item.source }}</p></el-collapse-item
          ></el-collapse
        >
        <h3>草稿原文</h3>
        <p class="draft-body">{{ activeDraft.body }}</p>
        <template v-if="activeDraft.status === 'PENDING_REVIEW'"
          ><label for="final-body">人工最终回复</label
          ><el-input
            id="final-body"
            v-model="editText"
            type="textarea"
            :rows="5"
            maxlength="4000"
          /><el-input
            v-model="reviewReason"
            placeholder="审核原因（必填）"
            aria-label="审核原因"
            class="space-top"
          />
          <div class="inline space-top">
            <el-button @click="feedback('ACCEPTED')">采纳原文</el-button
            ><el-button type="primary" @click="feedback('EDITED')"
              >修改采纳</el-button
            ><el-button type="danger" plain @click="feedback('REJECTED')"
              >拒绝</el-button
            >
          </div></template
        ><template
          v-else-if="['ACCEPTED', 'EDITED'].includes(activeDraft.status)"
          ><p class="muted small">审核完成。点击下方按钮后才会发送给客户。</p>
          <p v-if="activeDraft.final_body" class="draft-body">
            {{ activeDraft.final_body }}
          </p>
          <el-button type="primary" @click="sendDraft"
            >发送已审核回复</el-button
          ></template
        ><el-alert
          v-else
          :title="
            activeDraft.status === 'SENT'
              ? '已发送，重复点击不会产生第二条消息'
              : '草稿已拒绝，可重新生成'
          "
          type="info"
          :closable="false"
      /></template>
      <div v-else class="evidence-empty">
        <el-icon :size="40"><Cpu /></el-icon>
        <p>
          {{
            session.capabilities?.llm === "enabled"
              ? "尚无待审核草稿"
              : "LLM 未启用"
          }}
        </p>
        <span>请先核对问题与知识依据</span>
      </div>
    </div>
    <div class="panel actions-panel">
      <el-collapse
        ><el-collapse-item title="工单操作与 SLA" name="operations"
          ><div v-if="sla" class="small muted">
            <p>首次响应截止：{{ date(sla.response_due_at) }}</p>
            <p>解决截止：{{ date(sla.resolution_due_at) }}</p>
          </div>
          <el-input
            v-model="reason"
            placeholder="操作原因（必填）"
            aria-label="操作原因"
          /><template v-if="staff"
            ><div class="inline space-top">
              <el-select v-model="category" aria-label="人工分类"
                ><el-option
                  v-for="(name, i) in labels"
                  :key="i"
                  :label="name"
                  :value="i" /></el-select
              ><el-button @click="classify">确认分类</el-button
              ><el-button v-if="admin" @click="assign">派单 / 重分配</el-button>
            </div>
            <div v-if="admin" class="inline space-top">
              <el-select v-model="priority" aria-label="优先级"
                ><el-option
                  v-for="i in 4"
                  :key="i"
                  :label="`P${i - 1}`"
                  :value="i - 1" /></el-select
              ><el-button @click="setPriority">调整优先级</el-button>
            </div></template
          >
          <div class="inline space-top">
            <el-select
              v-model="target"
              aria-label="目标状态"
              placeholder="目标状态"
              ><el-option
                v-for="value in nextStates"
                :key="value"
                :label="statuses[value]"
                :value="value" /></el-select
            ><el-button @click="transition">更新状态</el-button>
          </div>
          <p class="small muted">
            状态转换、重开时间窗和权限由后端校验。
          </p></el-collapse-item
        ></el-collapse
      >
    </div>
  </section>
</template>
