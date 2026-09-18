import { StrictMode, useEffect, useState } from "react";
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
  pageQuery,
  statuses,
  type Job,
  type JobStatus,
} from "./api";
import "./style.css";

function useRequest<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{ key: string; data?: T; error?: Error }>({
    key,
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ key });
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ key, data });
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
    return () => controller.abort();
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
function Loading() {
  return (
    <p role="status" className="empty">
      正在加载任务…
    </p>
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
          <nav className="pagination" aria-label="分页">
            <span>
              共 {result.meta.total} 项 · 第 {result.meta.page} /{" "}
              {Math.max(
                1,
                Math.ceil(result.meta.total / result.meta.page_size),
              )}{" "}
              页
            </span>
            <button
              className="secondary"
              disabled={result.meta.page <= 1}
              onClick={() =>
                setParams(
                  pageQuery(params, { page: String(result.meta.page - 1) }),
                )
              }
            >
              上一页
            </button>
            <button
              className="secondary"
              disabled={
                result.meta.page * result.meta.page_size >= result.meta.total
              }
              onClick={() =>
                setParams(
                  pageQuery(params, { page: String(result.meta.page + 1) }),
                )
              }
            >
              下一页
            </button>
          </nav>
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
  } = useRequest(jobId, (signal) => api.detail(jobId, signal));
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
  return (
    <>
      <Link to="/">← 返回任务列表</Link>
      <h1>创建任务</h1>
      <section>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            const body = new FormData(event.currentTarget);
            if (!body.get("name")) body.delete("name");
            setBusy(true);
            setError("");
            try {
              const result = await api.create(body);
              navigate(`/jobs/${result.data.id}`);
            } catch (error) {
              setError(
                error instanceof ApiError
                  ? error.message
                  : "网络连接失败，请重试",
              );
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            任务名称（可选）
            <input name="name" maxLength={128} placeholder="留空自动生成" />
          </label>
          <label>
            CSV 文件
            <input name="file" type="file" accept=".csv" required />
          </label>
          <p className="muted">上传限制由服务端配置，默认 10 MB。</p>
          {error && <p role="alert">{error}</p>}
          <button disabled={busy}>{busy ? "正在创建…" : "上传并创建"}</button>
        </form>
      </section>
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
