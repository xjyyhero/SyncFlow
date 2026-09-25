import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  Route,
  Routes,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import {
  api,
  ApiError,
  formatTime,
  isTerminal,
  pageQuery,
  statuses,
  validateFile,
  type JobStatus,
  type PageMeta,
} from "./api";
import "./style.css";

function useRequest<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  shouldPoll?: (data: T) => boolean,
) {
  const [state, setState] = useState<{ key: string; data?: T; error?: Error }>({
    key,
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setState({ key });
    const run = () =>
      load(controller.signal).then(
        (data) => {
          if (controller.signal.aborted) return;
          setState({ key, data });
          if (shouldPoll?.(data)) timer = setTimeout(run, 3000);
        },
        (error) => {
          if (!controller.signal.aborted)
            setState({
              key,
              error:
                error instanceof ApiError
                  ? error
                  : new Error("网络连接失败，请检查连接后重试"),
            });
        },
      );
    void run();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
    // The key represents the complete request; changing it cancels stale responses.
  }, [key, attempt]);
  return {
    ...(state.key === key ? state : { key }),
    retry: () => setAttempt((n) => n + 1),
  };
}
function Status({ status }: { status: JobStatus }) {
  return (
    <span className={`badge ${status}`}>{statuses[status] ?? status}</span>
  );
}
function Failure({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <section role="alert">
      <h2>加载失败</h2>
      <p>{error.message}</p>
      <button onClick={retry}>重试</button>
    </section>
  );
}
function Loading({ text = "正在加载任务…" }: { text?: string }) {
  return (
    <p role="status" className="empty">
      {text}
    </p>
  );
}
function Pagination({ meta }: { meta: PageMeta }) {
  const [params, setParams] = useSearchParams();
  const go = (page: number) =>
    setParams(pageQuery(params, { page: String(page) }));
  return (
    <nav className="pagination" aria-label="分页">
      <span>
        共 {meta.total} 项 · 第 {meta.page} /{" "}
        {Math.max(1, Math.ceil(meta.total / meta.page_size))} 页
      </span>
      {meta.page > 1 && (
        <button className="secondary" onClick={() => go(1)}>
          第一页
        </button>
      )}
      <button
        className="secondary"
        disabled={meta.page <= 1}
        onClick={() => go(meta.page - 1)}
      >
        上一页
      </button>
      <button
        className="secondary"
        disabled={meta.page * meta.page_size >= meta.total}
        onClick={() => go(meta.page + 1)}
      >
        下一页
      </button>
    </nav>
  );
}
function List() {
  const [params, setParams] = useSearchParams();
  const query = params.toString();
  const {
    data: result,
    error,
    retry,
  } = useRequest(query, (signal) => api.list(query, signal));
  const status = params.get("status") ?? "";
  return (
    <>
      <div className="heading">
        <div>
          <p className="eyebrow">任务管理</p>
          <h1>任务列表</h1>
          <p>查看数据同步任务与处理结果</p>
        </div>
        <Link className="button" to="/jobs/new">
          创建任务
        </Link>
      </div>
      <section className="toolbar">
        <label>
          任务状态{" "}
          <select
            value={status}
            onChange={(event) =>
              setParams(
                pageQuery(params, { status: event.target.value, page: "1" }),
              )
            }
          >
            <option value="">全部状态</option>
            {Object.entries(statuses).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
            {status && !(status in statuses) && (
              <option value={status}>无效状态：{status}</option>
            )}
          </select>
        </label>
        <button className="secondary" onClick={retry}>
          刷新
        </button>
      </section>
      {error ? (
        <Failure error={error} retry={retry} />
      ) : !result ? (
        <Loading />
      ) : (
        <>
          {result.data.length === 0 ? (
            <section className="empty">
              <h2>暂无任务</h2>
              <p>
                {result.meta.total > 0
                  ? "当前页没有任务，请返回第一页。"
                  : "当前条件下没有任务，可以创建新任务或调整筛选。"}
              </p>
              <Link className="button" to="/jobs/new">
                创建任务
              </Link>{" "}
              <button
                className="secondary"
                onClick={() =>
                  setParams(pageQuery(params, { page: "1", status: "" }))
                }
              >
                查看全部任务
              </button>
            </section>
          ) : (
            <section className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {[
                      "任务名称",
                      "状态",
                      "源文件",
                      "总数",
                      "成功",
                      "失败",
                      "创建时间",
                      "操作",
                    ].map((text) => (
                      <th key={text}>{text}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.data.map((job) => (
                    <tr key={job.id}>
                      <td className="name">{job.name}</td>
                      <td>
                        <Status status={job.status} />
                      </td>
                      <td className="name">{job.source_file_name}</td>
                      <td>{job.total_records}</td>
                      <td>{job.success_records}</td>
                      <td>{job.failed_records}</td>
                      <td className="date">{formatTime(job.created_at)}</td>
                      <td>
                        <Link to={`/jobs/${encodeURIComponent(job.id)}`}>
                          详情
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          <Pagination meta={result.meta} />
        </>
      )}
    </>
  );
}
function Detail() {
  const { jobId = "" } = useParams();
  const {
    data: result,
    error,
    retry,
  } = useRequest(
    jobId,
    (signal) => api.detail(jobId, signal),
    (result) => !isTerminal(result.data.status),
  );
  const job = result?.data;
  return (
    <>
      <Link to="/">← 返回任务列表</Link>
      {error instanceof ApiError && error.code === "JOB_NOT_FOUND" ? (
        <section className="empty">
          <h1>任务不存在</h1>
          <p>该任务可能已删除，请返回列表查看。</p>
        </section>
      ) : error ? (
        <Failure error={error} retry={retry} />
      ) : !result ? (
        <Loading />
      ) : !job ? (
        <section className="empty">
          暂无任务数据 <button onClick={retry}>重试</button>
        </section>
      ) : (
        <>
          <div className="heading">
            <div>
              <p className="eyebrow">任务详情</p>
              <h1>{job.name}</h1>
              <p className="muted">{job.id}</p>
            </div>
            <Status status={job.status} />
          </div>
          <div className="stats">
            {[
              ["总记录数", job.total_records],
              ["成功记录", job.success_records],
              ["失败记录", job.failed_records],
              ["重试次数", job.retry_count],
            ].map(([label, value]) => (
              <section key={label}>
                <p>{label}</p>
                <strong>{value}</strong>
              </section>
            ))}
          </div>
          <section>
            <h2>任务信息</h2>
            <dl>
              {[
                ["源文件名", job.source_file_name],
                ["创建时间", formatTime(job.created_at)],
                ["开始时间", formatTime(job.started_at)],
                ["结束时间", formatTime(job.finished_at)],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            <p className="muted">时间按浏览器本地时区显示</p>
          </section>
          <section>
            <h2>最近错误</h2>
            {job.last_error_code || job.last_error_message ? (
              <>
                <p>{job.last_error_code}</p>
                <p>{job.last_error_message ?? "暂无错误说明"}</p>
              </>
            ) : (
              <p>暂无错误记录</p>
            )}
          </section>
          {(job.failed_records > 0 ||
            job.status === "FAILED" ||
            job.last_error_code) && (
            <p>
              <Link
                className="button"
                to={`/jobs/${encodeURIComponent(job.id)}/errors`}
              >
                查看错误明细
              </Link>
            </p>
          )}
          {!isTerminal(job.status) && (
            <p className="muted">每 3 秒自动刷新状态与统计</p>
          )}
          <button className="secondary" onClick={retry}>
            刷新任务
          </button>
        </>
      )}
    </>
  );
}
function Create() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const submitting = useRef(false);
  const fileError = file ? validateFile(file) : "";
  return (
    <>
      <Link to="/">← 返回任务列表</Link>
      <h1>创建任务</h1>
      <section>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (submitting.current) return;
            const body = new FormData(event.currentTarget);
            const validation = validateFile(file);
            if (validation) {
              setError(validation);
              return;
            }
            if (!body.get("name")) body.delete("name");
            submitting.current = true;
            setBusy(true);
            setError("");
            try {
              const result = await api.create(body);
              navigate(`/jobs/${encodeURIComponent(result.data.id)}`);
            } catch (error) {
              setError(
                error instanceof ApiError
                  ? error.message
                  : "网络连接失败，请重试",
              );
            } finally {
              submitting.current = false;
              setBusy(false);
            }
          }}
        >
          <label>
            任务名称（可选）
            <input
              name="name"
              maxLength={128}
              placeholder="留空自动生成"
              disabled={busy}
            />
          </label>
          <label>
            CSV 文件
            <input
              name="file"
              type="file"
              accept=".csv"
              required
              disabled={busy}
              aria-describedby="file-info file-limit"
              aria-invalid={Boolean(fileError)}
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setError("");
              }}
            />
          </label>
          <p id="file-info" className="file-info" role="status">
            {file
              ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB（${file.size.toLocaleString()} 字节）`
              : "尚未选择文件"}
          </p>
          <p id="file-limit" className="muted">
            仅支持 CSV，最大 10 MB。服务端会再次校验。
          </p>
          {(fileError || error) && <p role="alert">{fileError || error}</p>}
          <button disabled={busy || !file || Boolean(fileError)}>
            {busy ? "正在创建…" : "上传并创建"}
          </button>
        </form>
      </section>
    </>
  );
}
function Errors() {
  const { jobId = "" } = useParams();
  const [params] = useSearchParams();
  const query = params.toString();
  const {
    data: result,
    error,
    retry,
  } = useRequest(`${jobId}?${query}`, (signal) =>
    api.errors(jobId, query, signal),
  );
  return (
    <>
      <div className="breadcrumbs">
        <Link to={`/jobs/${encodeURIComponent(jobId)}`}>← 返回任务详情</Link>
        <Link to="/">任务列表</Link>
      </div>
      <div className="heading">
        <div>
          <p className="eyebrow">处理结果</p>
          <h1>错误明细</h1>
          <p className="muted">{jobId}</p>
        </div>
        <button className="secondary" onClick={retry}>
          刷新错误
        </button>
      </div>
      {error ? (
        <Failure error={error} retry={retry} />
      ) : !result ? (
        <Loading text="正在加载错误明细…" />
      ) : (
        <>
          {result.data.length === 0 ? (
            <section className="empty">
              <h2>{result.meta.total === 0 ? "暂无错误" : "当前页没有错误"}</h2>
              <p>
                {result.meta.total === 0
                  ? "该任务目前没有错误记录，处理中可稍后刷新。"
                  : "请返回第一页查看错误。"}
              </p>
            </section>
          ) : (
            <section className="table-wrap">
              <table>
                <caption className="muted">
                  行号按源文件定位；文件级错误显示 -。原始行可展开查看。
                </caption>
                <thead>
                  <tr>
                    {[
                      "行号",
                      "字段",
                      "错误码",
                      "错误消息",
                      "原始行摘要",
                      "记录时间",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.data.map((item, index) => {
                    const raw =
                      item.raw_row == null
                        ? null
                        : JSON.stringify(item.raw_row);
                    return (
                      <tr key={`${result.meta.page}-${index}`}>
                        <td>{item.row_number ?? "-"}</td>
                        <td>{item.field_name ?? "-"}</td>
                        <td className="name">{item.error_code}</td>
                        <td className="name">{item.error_message}</td>
                        <td className="raw-row">
                          {raw === null ? (
                            "-"
                          ) : (
                            <details>
                              <summary>
                                {raw.length > 100
                                  ? `${raw.slice(0, 100)}…`
                                  : raw}
                              </summary>
                              <pre>{raw}</pre>
                            </details>
                          )}
                        </td>
                        <td className="date">{formatTime(item.created_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}
          <Pagination meta={result.meta} />
        </>
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <header>
        <Link to="/" className="brand">
          SyncFlow
        </Link>
        <span>数据同步与任务管理</span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<List />} />
          <Route path="/jobs/new" element={<Create />} />
          <Route path="/jobs/:jobId" element={<Detail />} />
          <Route path="/jobs/:jobId/errors" element={<Errors />} />
          <Route
            path="*"
            element={
              <section className="empty">
                <p className="eyebrow">404</p>
                <h1>页面不存在</h1>
                <p>请检查访问地址。</p>
                <Link to="/">返回任务列表</Link>
              </section>
            }
          />
        </Routes>
      </main>
    </BrowserRouter>
  </StrictMode>,
);
