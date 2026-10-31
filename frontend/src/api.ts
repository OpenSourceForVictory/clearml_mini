const errors: Record<string, string> = {
  "batch control changed; refresh before retrying": "批次控制已变化，本次操作未执行，请核对最新状态",
  "suite not found": "评测集合不存在或无权访问",
  "suite must be published and not archived": "请选择已发布且未归档的集合",
  "suite run must use saved cases and client template": "集合运行必须使用其已保存清单和关联客户端",
  "unknown or disabled pool": "资源池不存在或已停用，请重新选择",
  "shared jobs require an enabled pool and positive VRAM request": "共享模式要求资源池允许共享，且每项显存申请大于零",
  "virtual pools do not allocate GPU memory": "模拟资源池不支持 GPU 共享或显存申请",
  "manifest exceeds remaining pending quota": "任务数超过当前剩余积压额度，请等待已有任务完成或减少清单项数",
  "unknown or disabled client template": "客户端模板不可用，请重新选择",
  "endpoint protocol does not match client template": "推理服务地址的协议与客户端模板不匹配",
  "client timeout exceeds platform execution limit": "每项总超时超过平台允许的执行时限",
  "environment override is not allowed by this template": "启动变量包含客户端模板不允许覆盖的键",
  "invalid credentials": "用户名或密码错误",
  "invalid token": "登录已过期，请重新登录",
  "pending limit": "排队任务已达上限，请等待部分任务完成",
  "previous execution cleanup is not confirmed":
    "正在等待 Worker 确认旧容器已停止，暂时无法重试",
  "node already provisioned": "该机器名称已注册",
  "username already exists": "用户名已存在",
  "pool name already exists": "资源池名称已存在",
};
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  extra: Record<string, string> = {},
): Promise<T> {
  const token = sessionStorage.getItem("eval-token");
  const res = await fetch("/api/v1/" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...extra,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await res.json();
  if (!res.ok) {
    if (res.status === 401 && path !== "auth/login")
      window.dispatchEvent(new Event("eval-logout"));
    throw new Error(
      (Array.isArray(value.detail) ? value.detail.map((x:{slot_id?:string;reason?:string}) => (x.slot_id||'')+': '+(x.reason||JSON.stringify(x))).join('; ') : errors[value.detail] || value.detail) || `请求失败 (${res.status})`,
    );
  }
  return value;
}
