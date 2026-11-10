<script setup>
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { api, run, session, date } from "../api";
const documents = ref([]),
  page = ref(1),
  loading = ref(false),
  query = ref(""),
  strategy = ref("rerank"),
  result = ref(null),
  editing = ref(false),
  selected = ref(null),
  key = ref(""),
  title = ref(""),
  content = ref(""),
  source = ref(""),
  from = ref(new Date().toISOString()),
  until = ref(""),
  reason = ref("");
const fileInput = ref(null);
async function importText(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (!/\.(txt|md)$/i.test(file.name) || file.size > 256000) {
    ElMessage.error("只接受不超过256KB的UTF-8 txt/md文件");
    event.target.value = "";
    return;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(
      await file.arrayBuffer(),
    );
    newVersion();
    content.value = text;
    title.value = file.name.replace(/\.[^.]+$/, "").slice(0, 200);
    key.value =
      title.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") ||
      `doc-${crypto.randomUUID().slice(0, 8)}`;
    source.value = `ADMIN_TEXT_IMPORT:${file.name}`.slice(0, 200);
  } catch {
    ElMessage.error("文件不是有效UTF-8文本");
  }
  event.target.value = "";
}
async function load() {
  loading.value = true;
  await run(async () => {
    documents.value = (
      await api.get("/knowledge", { params: { page: page.value } })
    ).data;
  });
  loading.value = false;
}
function newVersion(doc) {
  key.value = doc?.document_key || "";
  title.value = doc?.title || "";
  content.value = doc?.content || "";
  source.value = doc?.source || "管理员编写";
  from.value = new Date().toISOString();
  until.value = "";
  editing.value = true;
}
async function save() {
  loading.value = true;
  await run(async () => {
    await api.post("/knowledge", {
      document_key: key.value,
      title: title.value,
      content: content.value,
      source: source.value,
      valid_from: from.value,
      valid_until: until.value || null,
    });
    editing.value = false;
    await load();
  });
  loading.value = false;
}
async function act(doc, action) {
  if (!reason.value) return ElMessage.warning("请填写审核或发布原因");
  await run(() =>
    api.post(`/knowledge/${doc.id}/actions`, {
      action,
      expected_version: doc.version,
      reason: reason.value,
    }),
  );
  await load();
}
async function search() {
  if (!query.value.trim()) return;
  loading.value = true;
  await run(async () => {
    result.value = (
      await api.post("/knowledge/search", {
        query: query.value,
        strategy: strategy.value,
      })
    ).data;
  });
  loading.value = false;
}
async function build() {
  loading.value = true;
  await run(async () => {
    await api.post("/knowledge/index");
    ElMessage.success("新索引构建成功并激活");
  });
  loading.value = false;
}
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>知识库</h1>
      <p>有效政策、清晰版本、可追踪的知识依据</p>
    </div>
    <div v-if="session.user.role === 'ADMIN'" class="inline">
      <el-button @click="build" :loading="loading">构建并激活索引</el-button
      ><el-button @click="fileInput.click()">导入文本文件</el-button
      ><input
        ref="fileInput"
        type="file"
        accept=".txt,.md"
        hidden
        @change="importText"
      /><el-button type="primary" @click="newVersion()">新建文档</el-button>
    </div>
  </div>
  <section class="panel padded" v-loading="loading">
    <div class="toolbar">
      <el-input
        v-model="query"
        aria-label="知识检索问题"
        placeholder="输入问题，检索当前有效知识"
        @keyup.enter="search"
      /><el-select v-model="strategy" aria-label="检索策略"
        ><el-option label="BM25" value="bm25" /><el-option
          label="BGE 向量"
          value="vector" /><el-option
          label="混合检索"
          value="hybrid" /><el-option
          label="混合 + Reranker"
          value="rerank" /></el-select
      ><el-button type="primary" @click="search">检索</el-button>
    </div>
    <template v-if="result"
      ><el-alert
        :title="
          result.sufficient
            ? '已检索到候选依据，请人工确认适用性'
            : '没有足够依据，请补充信息或人工核查'
        "
        :type="result.sufficient ? 'info' : 'warning'"
        :closable="false"
      />
      <article
        v-for="item in result.items"
        :key="item.chunk_id"
        class="search-result"
      >
        <h3>
          {{ item.title }} v{{ item.revision }}
          <span class="muted small">[{{ item.chunk_id }}]</span>
        </h3>
        <p>{{ item.text }}</p>
        <span class="small muted">来源：{{ item.source }}</span>
      </article>
      <p class="small muted">
        本次 {{ result.latency_ms }}ms · 分数是排序信号，不代表正确概率
      </p></template
    >
  </section>
  <section class="panel padded space-top">
    <div class="section-title">
      <h2>文档版本</h2>
      <el-button @click="load">刷新</el-button>
    </div>
    <p class="small muted">
      演示知识来自AI编写的虚构政策，未经过独立人工复核。编辑时创建新版本，原始内容保留。
    </p>
    <el-input
      v-if="session.user.role === 'ADMIN'"
      v-model="reason"
      aria-label="知识审核原因"
      placeholder="审核 / 发布 / 退役原因（操作前填写）"
      class="space-bottom"
    />
    <el-table
      :data="documents"
      empty-text="暂无知识文档"
      @row-click="(row) => (selected = row)"
      ><el-table-column
        prop="title"
        label="标题"
        min-width="200"
      /><el-table-column
        prop="revision"
        label="版本"
        width="70"
      /><el-table-column
        prop="status"
        label="状态"
        width="115"
      /><el-table-column label="生效时间" min-width="160"
        ><template #default="{ row }">{{
          date(row.valid_from)
        }}</template></el-table-column
      ><el-table-column label="操作" min-width="250"
        ><template #default="{ row }"
          ><el-button text @click.stop="selected = row">查看</el-button
          ><template v-if="session.user.role === 'ADMIN'"
            ><el-button text @click.stop="newVersion(row)">新版本</el-button
            ><el-button
              text
              v-if="row.status === 'DRAFT'"
              @click.stop="act(row, 'review')"
              >审核</el-button
            ><el-button
              text
              v-if="row.status === 'REVIEWED'"
              @click.stop="act(row, 'publish')"
              >发布</el-button
            ><el-button
              text
              v-if="row.status === 'PUBLISHED'"
              @click.stop="act(row, 'retire')"
              >退役</el-button
            ></template
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
        :disabled="documents.length < 50"
        @click="
          page++;
          load();
        "
        >下一页</el-button
      >
    </div>
  </section>
  <el-dialog
    :model-value="!!selected"
    title="文档详情"
    @close="selected = null"
    width="min(720px, 94vw)"
    ><template v-if="selected"
      ><h2>{{ selected.title }} v{{ selected.revision }}</h2>
      <p class="pre-wrap">{{ selected.content }}</p>
      <p class="small muted">{{ selected.source }}</p>
      <p>审核备注：{{ selected.review_note || "尚未审核" }}</p></template
    ></el-dialog
  >
  <el-dialog
    v-model="editing"
    title="创建不可变文档版本"
    width="min(680px, 94vw)"
    ><el-form label-position="top" @submit.prevent="save"
      ><el-form-item label="文档标识（英文、数字、短横线）"
        ><el-input v-model="key" /></el-form-item
      ><el-form-item label="标题"><el-input v-model="title" /></el-form-item
      ><el-form-item label="纯文本 / Markdown 内容"
        ><el-input
          v-model="content"
          type="textarea"
          :rows="7"
          maxlength="100000" /></el-form-item
      ><el-form-item label="来源与许可说明"
        ><el-input v-model="source" /></el-form-item
      ><el-form-item label="生效时间（带时区ISO时间）"
        ><el-input v-model="from" /></el-form-item
      ><el-form-item label="失效时间（可选）"
        ><el-input v-model="until" /></el-form-item
      ><el-button type="primary" native-type="submit" :loading="loading"
        >保存待审核版本</el-button
      ></el-form
    ></el-dialog
  >
</template>
