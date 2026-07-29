# Agent Team Protocol

## Original objective

在 `<benchmark-generator-worktree>` 当前 Git worktree 中，用一个
Claude Code Developer 与一个独立 Codex Reviewer 完成真实的
review/accept-or-reject/fix/re-review 循环，直到没有开放的 P0、P1、P2 或 P3 问题。

## Source of truth

依次以本 Run 的 `REQUEST.md`、仓库 schema 与
`office-episode-contract.md`、当前 Git-visible 工作区、实际测试/验证结果和
Agent-Team System Facts 为准。r4 Origin 审计与既有 `AT-AUDIT-001` 是必须独立处置的
证据，但不能覆盖当前文件系统或冻结 Facts。Handoff 中的描述和 Finding 同样必须由
接收方重新核验。

## Team roles

### developer

- Binding: external
- Harness: Claude Code
- Session policy: resume
- 可以修改业务文件和运行测试。
- 收到 Reviewer Finding 后逐项独立判断，明确标记 accepted 或 rejected。
- accepted Finding 必须修复并验证；rejected Finding 必须提供代码、契约、测试、
  冻结 Facts 或可复现行为依据。
- 每轮完成后必须 Handoff 给 reviewer，不得自行 Complete。
- 删除缓存后运行 Python 验证时，必须使用不会重新生成 Git-visible cache artifact 的
  参数或环境，并核对最终 Git-visible 路径。
- 修改 canonical schema、validator、requirements 或 example 时，必须同步维护所有
  Skill bundle 副本，除非直接证据表明不应复制。
- 不得修改 `.agent-team/` 控制材料或启动逃逸受管进程组的 daemon。

### reviewer

- Binding: external
- Harness: Codex
- Session policy: resume
- 只审查，不修改任何业务文件；可以运行只读命令和测试。
- 首轮独立审查当前完整 Git-visible 工作区，核验 r4 的 65→68 路径变化、3 个 `.pyc`
  的来源、既有 `AT-AUDIT-001` 和当前可复现影响，不得机械沿用旧结论。
- 每轮独立审查完整工作区，而不只验证 Developer Handoff 或旧 Finding。
- Finding 使用稳定 ID，并标注 P0、P1、P2、P3 或 P4、位置、影响、直接证据和建议。
- P0 至 P3 阻塞 Completion；P4 非阻塞但必须在 Completion 中披露。
- 有开放 P0 至 P3 时 Handoff 给 developer。
- Developer 可以 reject；Reviewer 必须根据新证据重新判断，不得机械坚持。
- 无开放 P0 至 P3 时运行最终合理检查并调用 Complete。
- Reviewer 是本 Run 唯一 Completion Authority。

## Initial role

reviewer

## Collaboration protocol

1. Codex Reviewer 独立审查完整代码、契约、数据样例、测试、当前 Git-visible 路径和
   r4 Origin 审计证据。
2. 有 P0 至 P3 Finding 时，Reviewer 将结构化 Finding Handoff 给 Claude Code
   Developer。
3. Developer 对每项 Finding 独立 accept/reject；修复 accepted 项、举证 rejected 项，
   再 Handoff 给 Reviewer。
4. Reviewer 恢复同一 Codex Session，重新审查完整版本并关闭、维持、调整或新增
   Finding。
5. Developer 的任何业务修改必须经过下一轮 Reviewer 审查。
6. 同一争议连续两轮没有新证据且无法消解时调用 Block，不得无限循环。
7. 不得为了演示跨 Harness 流程虚构 Finding；如果独立证据表明没有阻塞 Finding，
   Reviewer 可以直接 Complete，但必须明确处置 r4 缓存证据。

## Completion condition

Codex Reviewer 确认当前完整 Git-visible 工作区没有开放 P0、P1、P2 或 P3 Finding，
明确关闭或非阻塞处置 r4 缓存证据，现有测试、数据验证和 canonical/Skill bundle
一致性检查通过，并提交 Completion Package。P4 可以保留，但必须明确披露。

## Final delivery

Reviewer 的 Completion Package 必须包含 Harness/Session、Finding 生命周期、
Developer accept/reject 决策、实际修改、测试与数据验证、最终 Git-visible cache
状态、剩余 P4 和最终判断。Agent-Team 将 Completion Event 返回当前 Origin Session，
由 Origin 对照 Journal、Facts、Session、进程和工作区独立审计后交付。

## Session continuity

- developer 使用 `claude-code:resume:default`，后续 Developer Turn 恢复同一个 Claude
  Code Session。
- reviewer 使用 `codex:resume:default`，初审与 re-review 必须恢复同一个独立 Codex
  Session。
- 两个 `default` Launch Profile 都来自本机 Doctor 的显式闭集，不从角色语义推断。

## Shared context policy

每轮读取 `REQUEST.md`、本协议、当前冻结 `input.md`、当前工作区和独立 System Facts。
Kickoff、Handoff 与 Resume Payload 都是下一 Turn 的直接输入。只传递正式 Handoff，
不传递私有思维链；跨 Harness 的发送方判断必须重新验证。

## Block and resume policy

所有 Block 必须返回用户。只有可 Resume Block 在新的明确用户指令到达后，才能由
Origin 管理 Claim 选择目标 Role 并 Resume。Limit 与 Profile Changed Block、以及
Request/Protocol/Role/Binding/Workspace/Profile/Limit 变化都要求新 Run。
`recover` 只做确定性技术收口，不代表自动 Resume。

## Assumptions made during bootstrap

- “P3 和 P3 以上”解释为 P0、P1、P2、P3 均阻塞，P4 不阻塞。
- 用户未要求新增功能，业务目标限定为基于仓库既有契约和已验证缓存 Finding 的质量
  收口。
- 由于 r4 已经终态，本 Run 按不可变 Run 规则新建，不尝试恢复 r4。
- Reviewer 首轮启动是为了正式处置完成后 Origin 审计发现的直接证据，并非虚构
  Finding。
- 两个角色均为 External Binding；当前 Origin Session 仅负责 Bootstrap、等待和最终
  审计。
- Claude Code 的非模型认证探测在当前 CLI 版本返回 `unknown`；实际可用性由受管
  Claude Turn 的结构化启动与完成证据证明，明确认证失败则 Block。

## Safety limits

最多 12 个业务 Turn，Wall Time 为 7200 秒。所有角色串行共享同一 Git worktree；
运行期间不手工并发编辑。不得跟踪 `.agent-team/`，不得启用 Sparse Checkout 或新增
Gitlink，不得启动逃逸 Runner PGID 的后台 daemon。External Deadline 由 Supervisor
强制；达到上限或不可 Resume Block 后创建新 Run，不在线修改冻结配置。
