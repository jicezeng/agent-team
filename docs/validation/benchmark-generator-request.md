# Original Request

在本机 `<benchmark-generator-worktree>` 仓库验证 Agent-Team
v0.1 的真实协作闭环。

用户原始描述：

> 使用 1 个 Codex 进行开发，一个 Codex 进行 review。review 后内容给到
> developer 判断是否合理 accept；对于 accept 的问题就修复，然后再给到
> reviewer 进行 review。如此循环，直至没有 P3 和 P3 以上的问题。

本次业务任务是在不臆造新产品需求的前提下，对仓库当前实现做完整质量收口：

- 理解现有 schema、validator、examples、tests 和 skill bundles 的意图；
- 运行现有验证和测试；
- 修复能够由当前契约、代码行为或可复现测试证明的正确性、可靠性和维护性问题；
- 保持 canonical 文件与各 Skill bundle 副本的一致性；
- 最终由 Reviewer 确认当前完整工作区没有开放的 P0、P1、P2 或 P3 Finding。

不得把 `.agent-team/` 纳入 Git，也不得为了制造循环而虚构问题或无依据扩展需求。
