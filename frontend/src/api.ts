const errors: Record<string, string> = {
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
