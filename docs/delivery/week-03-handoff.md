# Week 3 第九节交付与发布交接

日期：2026-09-25。分支：`chore/week-03`。异步 CSV 导入、完整 Web、数据库/错误/计数验收已完成，证据见 [测试报告](week-03-tests.md)与 [AI Code Review](week-03-review.md)。

## 交付入口

- [PR #9：Week 3 真实 CSV 同步、错误明细与 V1.0 交付](https://github.com/xjyyhero/SyncFlow/pull/9)。
- [GitHub AI Review 记录](https://github.com/xjyyhero/SyncFlow/pull/9#pullrequestreview-5317201950)：COMMENTED，明确为 AI 作者自查。
- [V1.0 发布草稿](https://github.com/xjyyhero/SyncFlow/releases/tag/untagged-ba0fa612906ee592cf33)：附源码包及 SHA-256，目标固定为交付提交；未正式发布。草稿需要仓库权限才能查看。
- [V1.1 规划 Issue #8](https://github.com/xjyyhero/SyncFlow/issues/8)：先讨论可靠队列与中断任务恢复；正式发布后再开始开发。

## 人工验收与正式发布

1. 审阅 PR 和 AI 自查，处理人工意见后合并；AI 自查不替代人工审核，不自动合并。
2. 按 [演示稿](week-03-demo-retrospective.md)本人演示正常、部分成功、文件级失败及规则修改，并记录结果。
3. 审核和演示完成后核对发布草稿。若人工审核修改了源码，重新运行相关测试、打包和上传附件；不要发布旧包。
4. 将发布目标设为包含已审核代码的 main 提交，发布 `v1.0.0`，确认 Release 附件、远程标签及标签对应提交。记录最终发布 URL/提交，再勾选清单中的发布项。
5. 从已发布版本新建 V1.1 功能分支并关联 Issue #8；确认开始后再勾选衔接项。

当前发布草稿不等于正式发布，V1.1 Issue 的创建不等于开发已启动。没有将现场演示、人工审核或正式标签标记为完成。
