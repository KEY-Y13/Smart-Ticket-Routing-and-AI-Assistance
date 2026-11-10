import axios from "axios";
import { reactive } from "vue";
import { ElMessage } from "element-plus";

// Token stays in memory. Refresh returns to login; no credentials in browser storage.
export const session = reactive({ user: null, token: "", capabilities: null });
export const api = axios.create({ baseURL: "/api/v1", timeout: 190000 });
api.interceptors.request.use((config) => {
  if (session.token) config.headers.Authorization = `Bearer ${session.token}`;
  return config;
});
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      session.user = null;
      session.token = "";
    }
    return Promise.reject(error);
  },
);
export async function run(action) {
  try {
    return await action();
  } catch (error) {
    const detail = error.response?.data?.error;
    ElMessage.error(
      detail
        ? `${detail.message}（${detail.code}）`
        : "连接失败，请确认服务已启动",
    );
    return undefined;
  }
}
export const key = () => crypto.randomUUID();
export const labels = [
  "订单问题",
  "支付问题",
  "物流问题",
  "售后退款",
  "商品质量",
  "账户问题",
];
export const statuses = {
  NEW: "新建",
  PENDING_ASSIGNMENT: "待分配",
  PROCESSING: "处理中",
  WAITING_CUSTOMER: "待客户补充",
  WAITING_EXTERNAL: "等待第三方",
  RESOLVED: "已解决",
  CLOSED: "已关闭",
};
export const date = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
