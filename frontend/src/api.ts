export const statuses = {
  PENDING: "待处理",
  RUNNING: "处理中",
  SUCCESS: "成功",
  FAILED: "失败",
} as const;
export type JobStatus = keyof typeof statuses;
export interface Job {
  id: string;
  name: string;
  status: JobStatus;
  source_file_name: string;
  total_records: number;
  success_records: number;
  failed_records: number;
  retry_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
}
export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
}
export interface ResponseData<T, M = Record<string, unknown>> {
  data: T;
  meta: M;
}
export interface ErrorResponse {
  error: {
    code: string;
    message: string;
    details: { field: string; message: string }[];
  };
}
export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}
export async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, options);
  let body;
  try {
    body = await response.json();
  } catch {
    throw new ApiError("INVALID_RESPONSE", "服务响应异常，请稍后重试");
  }
  if (!response.ok) {
    const error = (body as Partial<ErrorResponse>)?.error;
    throw new ApiError(
      error?.code ?? "HTTP_ERROR",
      error?.message ?? "请求失败，请稍后重试",
    );
  }
  if (!body || !("data" in body) || !("meta" in body))
    throw new ApiError("INVALID_RESPONSE", "服务响应异常，请稍后重试");
  return body as T;
}
export const api = {
  list: (query: string, signal: AbortSignal) =>
    request<ResponseData<Job[], PageMeta>>(`/jobs?${query}`, { signal }),
  detail: (id: string, signal: AbortSignal) =>
    request<ResponseData<Job>>(`/jobs/${encodeURIComponent(id)}`, { signal }),
  create: (body: FormData) =>
    request<ResponseData<Pick<Job, "id" | "name" | "status" | "created_at">>>(
      "/jobs",
      { method: "POST", body },
    ),
};
export function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
export function pageQuery(
  current: URLSearchParams,
  changes: Record<string, string>,
): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const [key, value] of Object.entries(changes)) {
    if (value) next.set(key, value);
    else next.delete(key);
  }
  return next;
}
