<script setup>
import { onMounted, ref } from "vue";
import { api, run, labels, date } from "../api";
const data = ref(null),
  tab = ref("users"),
  loading = ref(false),
  dialog = ref(""),
  login = ref(""),
  password = ref(""),
  role = ref("CUSTOMER"),
  groupCode = ref(""),
  groupName = ref(""),
  groupId = ref(""),
  agentId = ref(""),
  canView = ref(false),
  canHandle = ref(true),
  category = ref(0),
  rank = ref(0),
  audit = ref([]);
async function load() {
  loading.value = true;
  await run(async () => {
    data.value = (await api.get("/admin/configuration")).data;
    audit.value = (await api.get("/admin/audit")).data;
  });
  loading.value = false;
}
async function submit() {
  await run(async () => {
    if (dialog.value === "user")
      await api.post("/admin/users", {
        login: login.value,
        password: password.value,
        role: role.value,
      });
    if (dialog.value === "group")
      await api.post("/admin/groups", {
        code: groupCode.value,
        name: groupName.value,
      });
    if (dialog.value === "member")
      await api.post(`/admin/groups/${groupId.value}/members`, {
        user_id: agentId.value,
        can_handle: canHandle.value,
        can_view_business: canView.value,
      });
    if (dialog.value === "rule")
      await api.post("/admin/routing-rules", {
        category: category.value,
        group_id: groupId.value,
        rank: rank.value,
      });
    dialog.value = "";
    password.value = "";
    await load();
  });
}
async function toggleUser(row) {
  await run(() =>
    api.patch(`/admin/users/${row.id}`, {
      expected_version: row.version,
      enabled: !row.enabled,
    }),
  );
  await load();
}
async function toggleAgent(row) {
  await run(() =>
    api.patch(`/agents/${row.user_id}/availability`, {
      expected_version: row.version,
      online: !row.online,
      capacity: row.capacity,
    }),
  );
  await load();
}
async function toggleRule(row) {
  await run(() =>
    api.patch(`/admin/routing-rules/${row.id}`, {
      enabled: !row.enabled,
      reason: "管理员调整派单规则可用性",
    }),
  );
  await load();
}
async function toggleGroup(row) {
  await run(() =>
    api.patch(`/admin/groups/${row.id}`, {
      expected_version: row.version,
      enabled: !row.enabled,
      reason: "管理员调整业务组可用性",
    }),
  );
  await load();
}
async function toggleMember(row, permission) {
  await run(() =>
    api.patch(`/admin/groups/${row.group_id}/members/${row.user_id}`, {
      can_handle: permission === "handle" ? !row.can_handle : row.can_handle,
      can_view_business:
        permission === "view" ? !row.can_view_business : row.can_view_business,
      reason: "管理员调整技能组授权",
    }),
  );
  await load();
}
const userName = (id) =>
  data.value?.users.find((u) => u.id === id)?.login || id;
const groupLabel = (id) =>
  data.value?.groups.find((g) => g.id === id)?.name || id;
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>系统配置</h1>
      <p>角色、技能组与派单规则，所有变更均留痕</p>
    </div>
    <el-button @click="load">刷新</el-button>
  </div>
  <section class="panel padded" v-loading="loading" v-if="data">
    <el-tabs v-model="tab">
      <el-tab-pane label="用户与客服" name="users"
        ><el-button type="primary" @click="dialog = 'user'">创建用户</el-button
        ><el-table :data="data.users"
          ><el-table-column prop="login" label="用户名" /><el-table-column
            prop="role"
            label="角色"
          /><el-table-column label="状态"
            ><template #default="{ row }">{{
              row.enabled ? "启用" : "停用"
            }}</template></el-table-column
          ><el-table-column label="操作"
            ><template #default="{ row }"
              ><el-button text @click="toggleUser(row)">{{
                row.enabled ? "停用" : "启用"
              }}</el-button></template
            ></el-table-column
          ></el-table
        >
        <h3>客服在线与容量</h3>
        <el-table :data="data.agents"
          ><el-table-column label="客服"
            ><template #default="{ row }">{{
              userName(row.user_id)
            }}</template></el-table-column
          ><el-table-column label="负载 / 容量"
            ><template #default="{ row }"
              >{{ row.active_load }} /
              <el-input-number
                v-model="row.capacity"
                :min="Math.max(1, row.active_load)"
                :max="100"
                size="small" /></template></el-table-column
          ><el-table-column label="操作"
            ><template #default="{ row }"
              ><el-button text @click="toggleAgent(row)">{{
                row.online ? "下线并保存容量" : "上线并保存容量"
              }}</el-button></template
            ></el-table-column
          ></el-table
        ></el-tab-pane
      >
      <el-tab-pane label="业务组与授权" name="groups"
        ><div class="inline">
          <el-button type="primary" @click="dialog = 'group'"
            >创建业务组</el-button
          ><el-button @click="dialog = 'member'">添加客服到业务组</el-button>
        </div>
        <el-table :data="data.groups"
          ><el-table-column prop="name" label="业务组" /><el-table-column
            prop="code"
            label="标识"
          /><el-table-column label="状态"
            ><template #default="{ row }">{{
              row.enabled ? "启用" : "停用"
            }}</template></el-table-column
          ><el-table-column label="操作"
            ><template #default="{ row }"
              ><el-button text @click="toggleGroup(row)">{{
                row.enabled ? "停用" : "启用"
              }}</el-button></template
            ></el-table-column
          ></el-table
        >
        <h3>技能与业务数据权限</h3>
        <el-table :data="data.members"
          ><el-table-column label="业务组"
            ><template #default="{ row }">{{
              groupLabel(row.group_id)
            }}</template></el-table-column
          ><el-table-column label="客服"
            ><template #default="{ row }">{{
              userName(row.user_id)
            }}</template></el-table-column
          ><el-table-column label="处理授权"
            ><template #default="{ row }"
              ><el-button text @click="toggleMember(row, 'handle')">{{
                row.can_handle ? "已授权，点击撤销" : "未授权，点击授予"
              }}</el-button></template
            ></el-table-column
          ><el-table-column label="业务查询"
            ><template #default="{ row }"
              ><el-button text @click="toggleMember(row, 'view')">{{
                row.can_view_business ? "已授权，点击撤销" : "未授权，点击授予"
              }}</el-button></template
            ></el-table-column
          ></el-table
        ></el-tab-pane
      >
      <el-tab-pane label="派单规则" name="rules"
        ><el-button type="primary" @click="dialog = 'rule'"
          >新增派单规则</el-button
        ><el-table :data="data.rules"
          ><el-table-column label="类别"
            ><template #default="{ row }">{{
              labels[row.category]
            }}</template></el-table-column
          ><el-table-column label="业务组"
            ><template #default="{ row }">{{
              groupLabel(row.group_id)
            }}</template></el-table-column
          ><el-table-column
            prop="rank"
            label="顺序（小值优先）"
          /><el-table-column label="操作"
            ><template #default="{ row }"
              ><el-button text @click="toggleRule(row)">{{
                row.enabled ? "停用" : "启用"
              }}</el-button></template
            ></el-table-column
          ></el-table
        ></el-tab-pane
      >
      <el-tab-pane label="后台任务" name="jobs"
        ><el-table :data="data.jobs" empty-text="暂无任务"
          ><el-table-column prop="id" label="任务ID" /><el-table-column
            prop="type"
            label="类型" /><el-table-column
            prop="status"
            label="状态" /><el-table-column
            prop="attempts"
            label="尝试次数" /><el-table-column
            prop="last_error"
            label="异常类型" /></el-table
      ></el-tab-pane>
      <el-tab-pane label="管理审计" name="audit"
        ><el-table :data="audit"
          ><el-table-column label="时间" min-width="170"
            ><template #default="{ row }">{{
              date(row.created_at)
            }}</template></el-table-column
          ><el-table-column
            prop="action"
            label="动作"
            min-width="210" /><el-table-column
            prop="target_type"
            label="目标类型" /><el-table-column prop="target_id" label="目标ID"
        /></el-table>
        <p class="small muted">
          当前显示最早100条管理审计；完整审计可按游标分页查询。
        </p></el-tab-pane
      >
    </el-tabs>
  </section>
  <el-dialog
    :model-value="!!dialog"
    :title="
      {
        user: '创建用户',
        group: '创建业务组',
        member: '添加技能组成员',
        rule: '新增派单规则',
      }[dialog]
    "
    @close="dialog = ''"
    width="min(520px,94vw)"
    ><el-form label-position="top" @submit.prevent="submit"
      ><template v-if="dialog === 'user'"
        ><el-form-item label="用户名（小写英文数字）"
          ><el-input v-model="login" /></el-form-item
        ><el-form-item label="密码（至少12字符）"
          ><el-input
            v-model="password"
            type="password"
            show-password /></el-form-item
        ><el-form-item label="角色"
          ><el-select v-model="role"
            ><el-option label="客户" value="CUSTOMER" /><el-option
              label="客服"
              value="AGENT" /><el-option
              label="管理员"
              value="ADMIN" /></el-select></el-form-item></template
      ><template v-if="dialog === 'group'"
        ><el-form-item label="业务组标识"
          ><el-input v-model="groupCode" /></el-form-item
        ><el-form-item label="业务组名称"
          ><el-input v-model="groupName" /></el-form-item></template
      ><template v-if="['member', 'rule'].includes(dialog)"
        ><el-form-item label="业务组"
          ><el-select v-model="groupId"
            ><el-option
              v-for="g in data.groups"
              :key="g.id"
              :label="g.name"
              :value="g.id" /></el-select></el-form-item></template
      ><template v-if="dialog === 'member'"
        ><el-form-item label="客服"
          ><el-select v-model="agentId"
            ><el-option
              v-for="u in data.users.filter((u) => u.role === 'AGENT')"
              :key="u.id"
              :label="u.login"
              :value="u.id" /></el-select></el-form-item
        ><el-checkbox v-model="canHandle">允许处理工单</el-checkbox
        ><el-checkbox v-model="canView"
          >允许查询关联业务记录</el-checkbox
        ></template
      ><template v-if="dialog === 'rule'"
        ><el-form-item label="类别"
          ><el-select v-model="category"
            ><el-option
              v-for="(name, i) in labels"
              :key="i"
              :label="name"
              :value="i" /></el-select></el-form-item
        ><el-form-item label="排序"
          ><el-input-number v-model="rank" :min="0" :max="100" /></el-form-item
      ></template>
      <div class="space-top">
        <el-button native-type="submit" type="primary">保存</el-button>
      </div></el-form
    ></el-dialog
  >
</template>
