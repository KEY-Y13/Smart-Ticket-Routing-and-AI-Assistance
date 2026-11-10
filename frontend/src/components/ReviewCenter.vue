<script setup>
import { ref, computed, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { api, run, labels, date } from "../api";

const collections = ref([]),
  collectionId = ref(""),
  status = ref("PENDING"),
  page = ref(1);
const rows = ref([]),
  total = ref(0),
  selected = ref(null),
  history = ref([]),
  loading = ref(false);
const busy = ref(false),
  tab = ref("items"),
  hint = ref(false),
  acknowledged = ref(false);
const label = ref(null),
  group = ref(""),
  rights = ref(""),
  corrected = ref("");
const verdict = ref(""),
  answerable = ref(null),
  documentKeys = ref(""),
  refusal = ref("UNKNOWN");
const reason = ref(""),
  freezeReason = ref(""),
  freezeAck = ref(false);
const snapshots = ref([]),
  releases = ref([]),
  artifacts = ref([]),
  snapshotId = ref("");
const manifest = ref(""),
  scope = ref("SHADOW"),
  releaseReason = ref(""),
  releaseAck = ref(false);
let sequence = 0,
  detailSequence = 0;
const current = computed(() =>
  collections.value.find((c) => c.id === collectionId.value),
);
const statusNames = {
  PENDING: "待复核",
  APPROVED: "已确认",
  REJECTED: "已排除",
};
const scopeNames = { SHADOW: "影子验证 · 保持人工分流", LIVE: "正式自动分流" };
const display = (row) =>
  row.payload.text ||
  row.payload.title ||
  row.payload.query ||
  row.payload.question;

async function refreshMeta() {
  const [c, s, r, a] = await Promise.all([
    api.get("/reviews/collections"),
    api.get("/reviews/snapshots"),
    api.get("/reviews/releases"),
    api.get("/reviews/artifacts"),
  ]);
  collections.value = c.data;
  snapshots.value = s.data;
  releases.value = r.data;
  artifacts.value = a.data;
  if (!collectionId.value && c.data.length) {
    collectionId.value = c.data[0].id;
    if (!c.data[0].counts.PENDING) status.value = "";
  }
}
async function choose(row) {
  const token = ++detailSequence;
  selected.value = row;
  history.value = [];
  hint.value = false;
  acknowledged.value = false;
  reason.value = "";
  corrected.value = "";
  label.value = null;
  group.value = row.payload.group_id || "";
  rights.value =
    row.payload.rights_status === "unknown"
      ? ""
      : row.payload.rights_status || "";
  verdict.value = "";
  answerable.value = null;
  documentKeys.value = "";
  refusal.value = "UNKNOWN";
  await run(async () => {
    const { data } = await api.get(`/reviews/items/${row.id}/history`);
    if (token === detailSequence && selected.value?.id === row.id)
      history.value = data;
  });
}
async function loadRows() {
  const token = ++sequence;
  ++detailSequence;
  const id = collectionId.value;
  selected.value = null;
  history.value = [];
  rows.value = [];
  loading.value = true;
  await run(async () => {
    if (!id) return;
    const { data } = await api.get("/reviews/items", {
      params: {
        collection_id: id,
        status: status.value || undefined,
        page: page.value,
      },
    });
    if (token !== sequence || id !== collectionId.value) return;
    rows.value = data.items;
    total.value = data.total;
    if (data.items.length) await choose(data.items[0]);
  });
  if (token === sequence) loading.value = false;
}
async function changeCollection() {
  page.value = 1;
  await loadRows();
}
async function save(decision) {
  if (busy.value || !selected.value) return;
  if (!acknowledged.value || reason.value.trim().length < 5)
    return ElMessage.warning("请亲自核对，并填写至少5字的复核理由");
  let annotation = {};
  if (decision === "APPROVED") {
    if (current.value.kind === "CLASSIFICATION") {
      if (label.value === null || !group.value.trim() || !rights.value)
        return ElMessage.warning("请选择标签、对话分组和数据使用范围");
      annotation = {
        label: label.value,
        group_id: group.value.trim(),
        rights_status: rights.value,
        corrected_text: corrected.value.trim() || null,
      };
    } else if (current.value.kind === "KNOWLEDGE") {
      if (!verdict.value) return ElMessage.warning("请选择政策判定");
      annotation = { verdict: verdict.value };
    } else if (current.value.kind === "RETRIEVAL") {
      if (answerable.value === null)
        return ElMessage.warning("请选择是否可回答");
      annotation = {
        answerable: answerable.value,
        relevant_document_keys: answerable.value
          ? documentKeys.value
              .split(/[,，\n]/)
              .map((v) => v.trim())
              .filter(Boolean)
          : [],
      };
    } else {
      if (!verdict.value) return ElMessage.warning("请选择回复质量判定");
      annotation = {
        verdict: verdict.value,
        refusal_correct:
          refusal.value === "UNKNOWN" ? null : refusal.value === "YES",
      };
    }
  }
  const item = selected.value;
  busy.value = true;
  await run(async () => {
    await api.post(`/reviews/items/${item.id}/decisions`, {
      expected_version: item.version,
      decision,
      annotation,
      reason: reason.value.trim(),
      personally_reviewed: true,
    });
    await refreshMeta();
    await loadRows();
    ElMessage.success("复核记录已保存，原始数据保持不变");
  });
  busy.value = false;
}
async function freeze() {
  if (!freezeAck.value || !freezeReason.value.trim())
    return ElMessage.warning("请填写冻结说明并确认使用范围");
  busy.value = true;
  await run(async () => {
    await api.post(`/reviews/collections/${collectionId.value}/freeze`, {
      reason: freezeReason.value.trim(),
      acknowledged: true,
    });
    await refreshMeta();
    ElMessage.success("已生成不可变冻结版本");
  });
  busy.value = false;
}
async function download(row) {
  await run(async () => {
    const response = await api.get(`/reviews/snapshots/${row.id}/download`, {
      responseType: "blob",
    });
    const url = URL.createObjectURL(response.data),
      link = document.createElement("a");
    link.href = url;
    link.download = `review-${row.id}.jsonl`;
    link.click();
    URL.revokeObjectURL(url);
  });
}
async function register() {
  if (!snapshotId.value || !manifest.value)
    return ElMessage.warning("请选择分类冻结版本和训练产物");
  busy.value = true;
  await run(async () => {
    await api.post("/reviews/releases", {
      snapshot_id: snapshotId.value,
      manifest_ref: manifest.value,
      scope: scope.value,
    });
    await refreshMeta();
    ElMessage.success("候选已登记，请检查发布门槛");
  });
  busy.value = false;
}
async function decideRelease(row, action) {
  if (!releaseAck.value || releaseReason.value.trim().length < 5)
    return ElMessage.warning("请亲自检查证据并填写审批理由");
  busy.value = true;
  await run(async () => {
    await api.post(`/reviews/releases/${row.id}/decisions`, {
      expected_version: row.version,
      action,
      reason: releaseReason.value.trim(),
      personally_reviewed: true,
    });
    await refreshMeta();
    ElMessage.success("审批记录已保存");
  });
  busy.value = false;
}
onMounted(async () => {
  await run(refreshMeta);
  await loadRows();
});
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>数据与评审</h1>
      <p>逐条确认真值，冻结版本，凭证据审批模型</p>
    </div>
    <el-button
      @click="
        async () => {
          await run(refreshMeta);
          await loadRows();
        }
      "
      >刷新</el-button
    >
  </div>
  <el-alert
    title="复核结果用于离线数据与评测。AI复核会单独标识，不代表人工认证；人工提交须由实际复核人确认。"
    type="info"
    :closable="false"
  />
  <el-tabs v-model="tab" class="space-top">
    <el-tab-pane label="样本复核" name="items">
      <div class="panel padded space-top">
        <div class="review-filters">
          <el-select
            v-model="collectionId"
            aria-label="复核批次"
            @change="changeCollection"
            ><el-option
              v-for="c in collections"
              :key="c.id"
              :label="c.title"
              :value="c.id"
          /></el-select>
          <el-select
            v-model="status"
            aria-label="复核状态"
            @change="changeCollection"
            ><el-option label="全部状态" value="" /><el-option
              v-for="(name, key) in statusNames"
              :key="key"
              :label="name"
              :value="key"
          /></el-select>
        </div>
        <template v-if="current"
          ><p class="small muted">
            待复核 {{ current.counts.PENDING }} · 已确认
            {{ current.counts.APPROVED }} · 已排除 {{ current.counts.REJECTED }}
          </p>
          <p class="small muted review-ref">
            来源：{{ current.provenance }}
          </p></template
        >
        <el-empty
          v-if="!collections.length"
          description="尚无候选批次，请先导入获准使用的数据"
        />
      </div>
      <div class="review-layout space-top">
        <section class="panel padded" v-loading="loading">
          <el-table
            :data="rows"
            @row-click="choose"
            highlight-current-row
            style="width: 100%"
          >
            <el-table-column label="样本" min-width="250"
              ><template #default="{ row }"
                ><el-button
                  link
                  class="review-title"
                  @click.stop="choose(row)"
                  :aria-label="`查看 ${row.item_key}`"
                  >{{ display(row).slice(0, 55) }}</el-button
                >
                <p class="small muted">{{ row.item_key }}</p></template
              ></el-table-column
            >
            <el-table-column label="状态" width="90"
              ><template #default="{ row }">{{
                statusNames[row.status]
              }}</template></el-table-column
            >
            <el-table-column label="复核方式" width="110">
              <template #default="{ row }">{{
                row.latest_decision
                  ? row.latest_decision.review_method === "AI"
                    ? "AI复核"
                    : "人工复核"
                  : "待复核"
              }}</template>
            </el-table-column>
          </el-table>
          <el-pagination
            layout="total, prev, pager, next"
            :total="total"
            :page-size="20"
            v-model:current-page="page"
            @current-change="loadRows"
            class="space-top"
          />
        </section>
        <section class="panel padded review-detail" v-if="selected">
          <h2>{{ selected.item_key }} · 版本 {{ selected.version }}</h2>
          <p class="pre-wrap">
            {{
              selected.payload.text ||
              selected.payload.query ||
              selected.payload.question ||
              selected.payload.title
            }}
          </p>
          <template v-if="selected.kind === 'CLASSIFICATION'">
            <el-form label-position="top"
              ><el-form-item label="人工分类"
                ><el-select
                  v-model="label"
                  aria-label="复核分类"
                  placeholder="先独立选择标签"
                  ><el-option
                    v-for="(name, i) in labels"
                    :key="i"
                    :label="name"
                    :value="i" /></el-select
              ></el-form-item>
              <el-form-item label="对话或同源分组"
                ><el-input v-model="group" aria-label="复核分组"
              /></el-form-item>
              <p class="small muted">
                同一对话、改写或同源样本使用同一分组，避免分散到训练集与测试集。
              </p>
              <el-form-item label="数据使用范围"
                ><el-select v-model="rights" aria-label="数据权利"
                  ><el-option
                    label="仅本项目内部使用"
                    value="project_internal" /><el-option
                    label="已核实明确许可"
                    value="licensed" /></el-select
              ></el-form-item>
              <el-form-item label="修正文案（选填，原始文本仍保留）"
                ><el-input
                  v-model="corrected"
                  type="textarea"
                  :rows="3"
                  maxlength="4000" /></el-form-item
            ></el-form>
          </template>
          <template v-else-if="selected.kind === 'KNOWLEDGE'">
            <p class="pre-wrap review-evidence">
              {{ selected.payload.content }}
            </p>
            <el-select
              v-model="verdict"
              aria-label="政策判定"
              placeholder="请选择政策判定"
              ><el-option label="演示场景可用" value="DEMO_VALID" /><el-option
                label="内容不适用"
                value="INVALID" /><el-option
                label="信息不足，无法确认"
                value="UNDETERMINED"
            /></el-select>
          </template>
          <template v-else-if="selected.kind === 'RETRIEVAL'">
            <el-select
              v-model="answerable"
              aria-label="是否可回答"
              placeholder="请选择是否可回答"
              ><el-option label="有依据，可回答" :value="true" /><el-option
                label="应拒答"
                :value="false"
            /></el-select>
            <el-input
              v-if="answerable"
              v-model="documentKeys"
              class="space-top"
              aria-label="相关文档标识"
              placeholder="填写知识库文档标识，可用逗号或换行分隔"
              type="textarea"
              :rows="3"
            />
          </template>
          <template v-else>
            <el-alert
              v-if="selected.payload.refused"
              :title="`该案例被拒答：${selected.payload.error_code || '未提供可靠依据'}`"
              type="warning"
              :closable="false"
            />
            <p class="pre-wrap review-evidence">
              {{
                selected.payload.output ||
                "没有保存可供审查的草稿正文，请保留不确定结论。"
              }}
            </p>
            <el-collapse
              ><el-collapse-item
                v-for="e in selected.payload.evidence"
                :key="e.chunk_id"
                :title="`${e.title} [${e.chunk_id}]`"
                ><p class="pre-wrap">{{ e.text }}</p></el-collapse-item
              ></el-collapse
            >
            <el-select
              v-model="verdict"
              aria-label="回复质量判定"
              placeholder="请选择回复质量"
              ><el-option label="可以接受" value="ACCEPTABLE" /><el-option
                label="存在错误"
                value="INCORRECT" /><el-option
                label="无法判断"
                value="UNDETERMINED"
            /></el-select>
            <el-select
              v-if="selected.payload.refused"
              v-model="refusal"
              aria-label="拒答是否正确"
              class="space-top"
              ><el-option
                label="尚无法判断拒答是否正确"
                value="UNKNOWN" /><el-option
                label="拒答正确"
                value="YES" /><el-option label="拒答错误" value="NO"
            /></el-select>
          </template>
          <div class="space-top">
            <el-button text @click="hint = !hint">{{
              hint ? "收起原标注" : "显示原标注（仅供参考）"
            }}</el-button>
            <pre v-if="hint" class="review-ref small">{{
              JSON.stringify(selected.payload, null, 2)
            }}</pre>
          </div>
          <label for="review-reason">复核理由</label
          ><el-input
            id="review-reason"
            v-model="reason"
            type="textarea"
            :rows="3"
            maxlength="1000"
            placeholder="至少5字，说明你的判断依据"
          />
          <el-checkbox v-model="acknowledged" class="space-top"
            >我已亲自核对本条内容、标注与使用范围</el-checkbox
          >
          <div class="inline space-top">
            <el-button type="primary" :loading="busy" @click="save('APPROVED')"
              >确认标注</el-button
            ><el-button :disabled="busy" @click="save('REJECTED')"
              >排除此样本</el-button
            >
          </div>
          <el-collapse class="space-top"
            ><el-collapse-item title="复核历史"
              ><article v-for="h in history" :key="h.id">
                <p>
                  {{ h.review_method === "AI" ? "AI复核" : "人工复核" }} ·
                  {{ h.reviewer_login }} · {{ statusNames[h.decision] }} ·
                  {{ date(h.created_at) }}
                </p>
                <p>{{ h.reason }}</p>
                <pre class="small review-ref">{{
                  JSON.stringify(h.annotation, null, 2)
                }}</pre>
              </article>
              <p v-if="!history.length" class="muted">
                尚无复核记录
              </p></el-collapse-item
            ></el-collapse
          >
        </section>
      </div>
    </el-tab-pane>
    <el-tab-pane label="冻结与导出" name="snapshots"
      ><section class="panel padded">
        <h2>冻结已确认样本</h2>
        <p>
          只包含当前批次中有复核记录的已确认样本。待复核与已排除样本不会进入导出，原始数据不会被覆盖。
        </p>
        <p>
          当前批次：{{ current?.title || "请先选择批次" }} · 已确认
          {{ current?.counts.APPROVED || 0 }} 条
        </p>
        <el-input
          v-model="freezeReason"
          aria-label="冻结说明"
          placeholder="本次冻结的用途与范围"
        /><el-checkbox v-model="freezeAck" class="space-top"
          >我已确认此版本的使用范围</el-checkbox
        >
        <p>
          <el-button
            type="primary"
            :loading="busy"
            :disabled="!current?.counts.APPROVED"
            @click="freeze"
            >冻结已确认样本</el-button
          >
        </p>
        <el-table :data="snapshots"
          ><el-table-column label="版本" width="85"
            ><template #default="{ row }"
              >#{{ row.id }}</template
            ></el-table-column
          ><el-table-column
            prop="row_count"
            label="样本数"
            width="85"
          /><el-table-column
            prop="snapshot_sha256"
            label="内容指纹"
            min-width="240"
            show-overflow-tooltip
          /><el-table-column label="操作" width="110"
            ><template #default="{ row }"
              ><el-button link @click="download(row)"
                >下载 JSONL</el-button
              ></template
            ></el-table-column
          ></el-table
        >
      </section></el-tab-pane
    >
    <el-tab-pane label="模型审批" name="releases"
      ><section class="panel padded">
        <h2>登记受信任训练产物</h2>
        <p>
          仅RF/LR本机训练目录可登记。影子验证保留候选分数并继续人工分流；正式自动分流另需至少1200条人审数据、独立测试和质量门槛。
        </p>
        <div class="review-filters">
          <el-select
            v-model="snapshotId"
            aria-label="分类冻结版本"
            placeholder="请选择分类冻结版本"
            ><el-option
              v-for="s in snapshots.filter(
                (s) =>
                  collections.find((c) => c.id === s.collection_id)?.kind ===
                  'CLASSIFICATION',
              )"
              :key="s.id"
              :label="`冻结 #${s.id} · ${s.row_count}条`"
              :value="s.id"
          /></el-select>
          <el-select
            v-model="manifest"
            aria-label="训练产物"
            placeholder="请选择训练产物"
            ><el-option
              v-for="a in artifacts"
              :key="a.manifest_ref"
              :label="`${a.name} · ${a.purpose}`"
              :value="a.manifest_ref"
          /></el-select>
          <el-select v-model="scope" aria-label="发布范围"
            ><el-option
              v-for="(name, key) in scopeNames"
              :key="key"
              :label="name"
              :value="key"
          /></el-select>
        </div>
        <p v-if="manifest" class="review-ref small">{{ manifest }}</p>
        <el-button :loading="busy" @click="register">登记候选</el-button>
        <el-empty
          v-if="!snapshots.length"
          description="尚无冻结版本；AI复核与人工金标准会分别记录，smoke模型不能上线"
        />
        <div class="space-top">
          <el-input
            v-model="releaseReason"
            aria-label="模型审批理由"
            placeholder="检查数据与评测证据后填写审批理由（至少5字）"
          /><el-checkbox v-model="releaseAck"
            >我已亲自检查冻结数据、评测和发布范围</el-checkbox
          >
        </div>
        <article
          v-for="r in releases"
          :key="r.id"
          class="review-release space-top"
        >
          <h3>候选 #{{ r.id }} · {{ scopeNames[r.scope] }} · {{ r.status }}</h3>
          <p class="small review-ref">{{ r.manifest_ref }}</p>
          <p
            v-for="g in r.gates"
            :key="g.code"
            :class="g.passed ? 'review-pass' : 'review-fail'"
          >
            {{ g.passed ? "通过" : "未通过" }} · {{ g.message }}
          </p>
          <div class="inline">
            <el-button
              v-if="r.status === 'CANDIDATE'"
              type="primary"
              :disabled="busy || r.gates.some((g) => !g.passed)"
              @click="decideRelease(r, 'APPROVE')"
              >批准发布包</el-button
            ><el-button
              v-if="r.status === 'CANDIDATE'"
              :disabled="busy"
              @click="decideRelease(r, 'REJECT')"
              >拒绝候选</el-button
            ><el-button
              v-if="r.status === 'APPROVED'"
              :disabled="busy"
              @click="decideRelease(r, 'REVOKE')"
              >撤销审批</el-button
            >
          </div>
          <p v-if="r.package_ref" class="review-ref small">
            批准包：{{
              r.package_ref
            }}。需由部署人员显式配置后生效，审批不会自动切换正在运行的模型。
          </p>
        </article>
      </section></el-tab-pane
    >
  </el-tabs>
</template>

<style scoped>
.review-filters {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.review-filters .el-select {
  width: 260px;
  max-width: 100%;
}
.review-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.review-detail {
  min-width: 0;
}
.review-title {
  max-width: 100%;
  height: auto;
  text-align: left;
}
.review-title :deep(span) {
  white-space: normal;
  overflow-wrap: anywhere;
}
.review-ref {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.review-evidence {
  background: #f6f8fa;
  padding: 14px;
  border-radius: 6px;
}
.review-release {
  border-top: 1px solid #e5eaf0;
  padding-top: 16px;
}
.review-pass {
  color: #236556;
}
.review-fail {
  color: #925922;
}
.el-checkbox {
  max-width: 100%;
  height: auto;
  min-height: 32px;
}
.el-checkbox :deep(.el-checkbox__label) {
  white-space: normal;
}
@media (max-width: 1100px) {
  .review-layout {
    grid-template-columns: 1fr;
  }
}
</style>
