import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import "./style.css";

function Home() {
  return (
    <main>
      <p className="eyebrow">WEEK 1 · 项目启动</p>
      <h1>SyncFlow</h1>
      <p>数据同步与任务管理服务</p>
      <section>
        <h2>开发环境已就绪</h2>
        <p>当前为项目骨架，CSV 上传、任务处理和结果查询将在后续实现。</p>
        <a href="/api/v1/info">查看服务信息</a>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route
          path="*"
          element={
            <main>
              <h1>页面不存在</h1>
              <Link to="/">返回首页</Link>
            </main>
          }
        />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
