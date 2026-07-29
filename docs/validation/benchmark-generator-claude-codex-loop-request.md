# Original Request

在本机 `<benchmark-generator-worktree>` 仓库验证 Agent-Team
v0.1 的 Claude Code / Codex 混合协作闭环。

用户原始描述：

> 使用 1 个 Claude Code 进行开发，一个 Codex 进行 review。review 后内容给到
> developer 判断是否合理 accept；对于 accept 的问题就修复，然后再给到 reviewer
> 进行 review。如此循环，直至没有 P3 和 P3 以上的问题。

本 Run 是 `at-benchmark-claude-codex-r4` 完成后的质量续跑。r4 的 Origin 审计发现：

- Developer Turn 前后 Git-visible business path 数从 65 变为 68；
- 新增的 3 个路径是 pytest 生成的
  `scripts/__pycache__/validate_dataset.cpython-39.pyc`、
  `tests/__pycache__/test_skill_bundles.cpython-39-pytest-8.3.5.pyc` 和
  `tests/__pycache__/test_validate_dataset.cpython-39-pytest-8.3.5.pyc`；
- 仓库既有验证报告中的 `AT-AUDIT-001` 已把同类 Git-visible 缓存残留定为 P3，
  Developer 曾明确 accept 并清理，Reviewer 随后关闭 Finding。

这些材料是待独立核验的直接证据，不替代当前文件系统或 Reviewer 判断。本次必须：

- Codex Reviewer 先独立审查当前完整 Git-visible 工作区并明确处置上述证据；
- 若存在 P0 至 P3 Finding，正式 Handoff 给 Claude Code Developer；
- Developer 对每项 Finding 明确 accept 或 reject，修复 accepted 项并举证 rejected
  项；
- Developer 的任何业务修改必须交回同一个 Codex Reviewer Session re-review；
- 最终仅在当前完整工作区没有开放 P0、P1、P2 或 P3 Finding 时由 Reviewer
  Complete。

不得为了制造循环虚构 Finding，不得把 `.agent-team/` 纳入 Git，也不得扩展到仓库既有
契约之外的新产品功能。
