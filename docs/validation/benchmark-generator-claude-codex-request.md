# Original Request

在本机 `<benchmark-generator-worktree>` 仓库验证 Agent-Team
v0.1 的 Claude Code / Codex 混合协作闭环。

用户原始描述：

> 使用 1 个 Claude Code 进行开发，一个 Codex 进行 review。review 后内容给到
> developer 判断是否合理 accept；对于 accept 的问题就修复，然后再给到 reviewer
> 进行 review。如此循环，直至没有 P3 和 P3 以上的问题。

本次业务任务是在不臆造新产品需求的前提下，对仓库当前完整 Git-visible 实现做质量
收口：

- Claude Code Developer 独立理解现有 schema、validator、examples、tests 和
  Skill bundles，运行验证并修复有直接证据的问题；
- Codex Reviewer 独立审查完整当前工作区，不把 Developer 自述当作事实；
- Reviewer 的 P0 至 P3 Finding 必须正式交给 Developer；
- Developer 对每个 Finding 明确 accept 或 reject，修复 accepted 项并举证 rejected
  项；
- Developer 的任何业务修改都必须再交给同一 Codex Reviewer Session re-review；
- 最终仅在没有开放 P0、P1、P2 或 P3 Finding 时由 Reviewer Complete。

不得为了制造循环虚构 Finding，不得把 `.agent-team/` 纳入 Git，也不得扩展到仓库既有
契约之外的新产品功能。
