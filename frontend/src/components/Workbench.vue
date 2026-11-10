<script setup>
import { ref, onMounted } from "vue";
import { Search, Refresh, Plus } from "@element-plus/icons-vue";
import { api, run, session, labels, statuses, key, date } from "../api";
import TicketDetail from "./TicketDetail.vue";
const rows = ref([]),
  selected = ref(null),
  loading = ref(false),
  total = ref(0),
  page = ref(1),
  q = ref(""),
  status = ref("");
const createOpen = ref(false),
  creating = ref(false),
  subject = ref(""),
  body = ref(""),
  order = ref("");
async function load() {
  loading.value = true;
  await run(async () => {
    const result = (
      await api.get("/tickets", {
        params: {
          page: page.value,
          q: q.value || undefined,
          status: status.value || undefined,
        },
      })
    ).data;
    rows.value = result.items;
    total.value = result.total;
    if (selected.value)
      selected.value =
        rows.value.find((row) => row.id === selected.value.id) ||
        selected.value;
  });
  loading.value = false;
}
function filter() {
  page.value = 1;
  load();
}
async function create() {
  if (!subject.value.trim() || body.value.trim().length < 6) return;
  creating.value = true;
  await run(async () => {
    const result = await api.post(
      "/tickets",
      {
        subject: subject.value,
        body: body.value,
        ...(order.value ? { order_no: order.value } : {}),
      },
      { headers: { "Idempotency-Key": key() } },
    );
    createOpen.value = false;
    subject.value = "";
    body.value = "";
    order.value = "";
    await load();
    selected.value = result.data;
  });
  creating.value = false;
}
async function dispatch() {
  await run(() =>
    api.post("/admin/dispatch-next", { reason: "管理员按优先级触发队列派单" }),
  );
  await load();
}
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>工单工作台</h1>
      <p>管理客户诉求，追踪每一次处理</p>
    </div>
    <el-button
      v-if="session.user.role === 'CUSTOMER'"
      type="primary"
      :icon="Plus"
      @click="createOpen = true"
      >新建工单</el-button
    ><el-button v-if="session.user.role === 'ADMIN'" @click="dispatch"
      >处理待分配队列</el-button
    >
  </div>
  <div class="workbench" :class="{ 'with-detail': selected }">
    <section class="panel ticket-list" v-loading="loading">
      <div class="toolbar">
        <el-input
          v-model="q"
          aria-label="搜索工单"
          placeholder="搜索工单"
          clearable
          :prefix-icon="Search"
          @keyup.enter="filter"
          @clear="filter"
        /><el-select
          v-model="status"
          aria-label="工单状态筛选"
          placeholder="全部状态"
          clearable
          @change="filter"
          ><el-option
            v-for="(label, value) in statuses"
            :key="value"
            :label="label"
            :value="value" /></el-select
        ><el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
      <el-table
        :data="rows"
        highlight-current-row
        :row-class-name="
          ({ row }) => (selected?.id === row.id ? 'selected-row' : '')
        "
        @row-click="(row) => (selected = row)"
        empty-text="暂无工单，提交工单后会显示在这里"
        row-key="id"
      >
        <el-table-column label="工单" min-width="190"
          ><template #default="{ row }"
            ><button class="ticket-link" @click="selected = row">
              {{ row.subject }}
            </button>
            <div class="small muted">{{ row.ticket_no }}</div></template
          ></el-table-column
        >
        <el-table-column label="状态" width="114"
          ><template #default="{ row }"
            ><el-tag :type="row.status === 'RESOLVED' ? 'success' : 'info'">{{
              statuses[row.status]
            }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="优先级" width="80"
          ><template #default="{ row }"
            ><el-tag :type="row.priority < 2 ? 'danger' : 'warning'"
              >P{{ row.priority }}</el-tag
            ></template
          ></el-table-column
        >
        <el-table-column label="分类" width="110"
          ><template #default="{ row }">{{
            labels[row.category] || "待人工分类"
          }}</template></el-table-column
        >
        <el-table-column label="创建时间" min-width="145"
          ><template #default="{ row }">{{
            date(row.created_at)
          }}</template></el-table-column
        >
      </el-table>
      <el-pagination
        v-model:current-page="page"
        :total="total"
        :page-size="20"
        layout="total, prev, pager, next"
        @current-change="load"
      />
    </section>
    <TicketDetail
      v-if="selected"
      :ticket-id="selected.id"
      @updated="load"
      @close="selected = null"
    />
  </div>
  <el-dialog v-model="createOpen" title="新建工单" width="min(560px, 94vw)"
    ><el-form label-position="top" @submit.prevent="create"
      ><el-form-item label="问题标题"
        ><el-input v-model="subject" maxlength="200" /></el-form-item
      ><el-form-item label="问题描述（至少6个字）"
        ><el-input
          v-model="body"
          type="textarea"
          :rows="5"
          maxlength="4000" /></el-form-item
      ><el-form-item label="订单编号（可选，仅限本人订单）"
        ><el-input
          v-model="order"
          placeholder="ST开头的演示订单号" /></el-form-item
      ><el-button type="primary" native-type="submit" :loading="creating"
        >提交工单</el-button
      ></el-form
    ></el-dialog
  >
</template>
