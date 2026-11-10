<script setup>
import { computed, ref, defineAsyncComponent } from "vue";
import {
  Tickets,
  Reading,
  CircleCheck,
  DataAnalysis,
  Setting,
  SwitchButton,
  EditPen,
} from "@element-plus/icons-vue";
import { api, run, session } from "./api";
import Workbench from "./components/Workbench.vue";
const Knowledge = defineAsyncComponent(
  () => import("./components/Knowledge.vue"),
);
const Operations = defineAsyncComponent(
  () => import("./components/Operations.vue"),
);
const Configuration = defineAsyncComponent(
  () => import("./components/Configuration.vue"),
);
const ReviewCenter = defineAsyncComponent(
  () => import("./components/ReviewCenter.vue"),
);
const page = ref("tickets"),
  busy = ref(false),
  login = ref(""),
  password = ref("");
const roles = { ADMIN: "管理员", AGENT: "客服", CUSTOMER: "客户" };
const availability = ref(null);
const nav = computed(() => [
  { id: "tickets", title: "工单工作台", icon: Tickets },
  ...(session.user?.role !== "CUSTOMER"
    ? [{ id: "knowledge", title: "知识库", icon: Reading }]
    : []),
  ...(session.user?.role === "ADMIN"
    ? [
        { id: "reviews", title: "数据与评审", icon: EditPen },
        { id: "sla", title: "SLA 管理", icon: CircleCheck },
        { id: "metrics", title: "数据统计", icon: DataAnalysis },
        { id: "config", title: "系统配置", icon: Setting },
      ]
    : []),
]);
async function signIn() {
  if (!login.value || !password.value) return;
  busy.value = true;
  await run(async () => {
    const { data } = await api.post("/auth/login", {
      login: login.value,
      password: password.value,
    });
    session.token = data.access_token;
    session.user = data.user;
    page.value = "tickets";
    password.value = "";
    session.capabilities = (await api.get("/capabilities")).data;
    availability.value =
      session.user.role === "AGENT"
        ? (await api.get(`/agents/${session.user.id}/availability`)).data
        : null;
  });
  busy.value = false;
}
async function signOut() {
  const success = await run(() => api.post("/auth/logout"));
  if (success) {
    session.user = null;
    session.token = "";
    page.value = "tickets";
  }
}
async function toggleAvailability() {
  await run(async () => {
    availability.value = (
      await api.patch(`/agents/${session.user.id}/availability`, {
        expected_version: availability.value.version,
        online: !availability.value.online,
      })
    ).data;
  });
}
</script>
<template>
  <div v-if="!session.user" class="login-layout">
    <section class="login-intro">
      <span class="brand">SmartTicket AI</span>
      <h1>让每一次处理<br />都有据可循。</h1>
      <p>工单分流 · 知识检索 · 人工审核</p>
      <div class="intro-line"></div>
      <p class="muted">虚拟 3C 电商售后场景<br />本地演示与技术验证</p>
    </section>
    <el-form class="login-form" label-position="top" @submit.prevent="signIn">
      <h2>登录工作台</h2>
      <p class="muted">使用已分配的账号进入对应工作空间</p>
      <el-form-item label="用户名"
        ><el-input v-model="login" autocomplete="username" aria-label="用户名"
      /></el-form-item>
      <el-form-item label="密码"
        ><el-input
          v-model="password"
          type="password"
          show-password
          autocomplete="current-password"
          aria-label="密码"
          @keyup.enter="signIn"
      /></el-form-item>
      <el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        class="full"
        >登录</el-button
      >
      <p class="muted small">
        账号由管理员分配。演示账号密码保存在项目本地私有配置中。
      </p>
    </el-form>
  </div>
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">SmartTicket AI</div>
      <nav aria-label="主导航">
        <button
          v-for="item in nav"
          :key="item.id"
          :class="{ active: page === item.id }"
          @click="page = item.id"
        >
          <el-icon><component :is="item.icon" /></el-icon>{{ item.title }}
        </button>
      </nav>
      <p class="sidebar-foot">虚拟业务演示</p>
    </aside>
    <div class="main-shell">
      <header class="identity">
        <el-button v-if="availability" text @click="toggleAvailability">{{
          availability.online ? "客服在线 · 点击下线" : "客服离线 · 点击上线"
        }}</el-button>
        <span>{{ roles[session.user.role] }} · {{ session.user.login }}</span
        ><el-button text :icon="SwitchButton" @click="signOut">退出</el-button>
      </header>
      <main>
        <Workbench v-if="page === 'tickets'" /><Knowledge
          v-else-if="page === 'knowledge'"
        /><Operations
          v-else-if="page === 'sla' || page === 'metrics'"
          :key="page"
          :mode="page"
        /><ReviewCenter v-else-if="page === 'reviews'" /><Configuration
          v-else
        />
      </main>
    </div>
  </div>
</template>
