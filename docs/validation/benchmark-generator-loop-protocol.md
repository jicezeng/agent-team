# Agent Team Protocol

## Original objective

在 `benchmark-generator` 当前 Git worktree 中完成现有实现的质量收口，并真实验证
一个 Codex Reviewer 与一个 Codex Developer 的
review/accept-or-reject/fix/re-review 循环，直到没有开放的 P0、P1、P2 或 P3 问题。

## Source of truth

依次以本 Run 的 `REQUEST.md`、仓库中的 schema 与
`office-episode-contract.md`、当前 Git-visible 工作区、实际命令结果和
Agent-Team System Facts 为准。上一 Run 的 Handoff/Completion 只是待核验材料。
Finding、发送方判断和“无变化”声明都不能覆盖冻结 Facts 或当前文件系统。

## Team roles

### reviewer

- Binding: external
- Harness: Codex
- Session policy: resume
- 只审查，不修改业务文件；可以运行只读命令和测试。
- 首轮独立复核 Request 中记录的 bytecode/Facts/Handoff 差异，并审查完整工作区。
- Finding 使用稳定 ID，标注 P0、P1、P2、P3 或 P4、位置、影响、证据和建议。
- 将该已记录的审计可靠性问题作为有直接证据的 Finding 正式 Handoff 给 Developer；
  不得因为修复看似简单而跳过 Developer 的 accept/reject。
- Developer 可以拒绝 Finding；Reviewer 必须在下一 Turn 根据新证据重新判断。
- Re-review 必须覆盖完整工作区，而不只检查上一项修复。
- 没有开放 P0 至 P3 Finding，且本 Run 已完成至少一次
  reviewer→developer→reviewer 路由后，运行最终合理检查并调用 Complete。
- Reviewer 是本 Run 的 Completion Authority。

### developer

- Binding: external
- Harness: Codex
- Session policy: resume
- 可以修改业务文件和运行测试。
- 收到 Reviewer Finding 后逐项独立判断并明确标记 accepted 或 rejected。
- accepted Finding 必须修复并用适当测试验证；验证命令不得重新留下未解释的
  Git-visible bytecode/cache。
- rejected Finding 必须给出代码、契约、测试、冻结 Facts或可复现行为依据。
- 处理完成后必须 Handoff 给 Reviewer，不得自行 Complete。
- 修改 canonical schema、validator、requirements 或 example 时，同步维护所有
  Skill bundle 副本，除非直接证据表明不应复制。

## Initial role

reviewer

## Collaboration protocol

1. Reviewer 独立检查完整工作区与上一 Run 的直接审计证据，提交稳定 Finding 并
   Handoff 给 Developer。
2. Developer 逐项 accept/reject；修复 accepted 项并验证，举证 rejected 项，然后
   Handoff 给 Reviewer。
3. Reviewer 使用同一 Codex Session 重新审查完整当前版本，关闭、维持、调整或新增
   Finding。
4. 若仍有开放 P0 至 P3 Finding，继续 Handoff 给 Developer；否则 Reviewer Complete。
5. Developer 的任何业务修改都必须经过下一轮 Reviewer 审查。
6. 同一争议若连续两轮没有新证据且无法消解，调用 Block 返回用户。
7. 不得虚构额外 Finding；已记录的 bytecode/Facts 差异是本 Run 唯一预先给出的
   直接问题证据。

## Completion condition

Reviewer 确认：

- 已实际发生至少一次 reviewer→developer→reviewer 正式路由；
- Developer 已对每个 Finding 明确 accept/reject；
- 当前完整 Git-visible 工作区没有开放 P0、P1、P2 或 P3 Finding；
- 不存在未解释的生成 bytecode/cache 或 Handoff 与冻结 Facts 冲突；
- 现有测试、数据验证与 canonical/Skill bundle 一致性检查通过。

P4 可以保留，但必须在 Completion 中披露。

## Final delivery

Reviewer 准备包含 Finding 生命周期、Developer 判断、实际修改、两轮独立验证、
Session 连续性与剩余 P4 的 Completion Package。Agent-Team 将 Completion Event
返回当前 Origin Session，由 Origin 对照 Journal、Facts、Session 和测试证据审计后
向用户交付。

## Session continuity

- reviewer 恢复同一个独立 Codex Session，以验证真实 re-review 连续性。
- developer 使用一个独立 Codex Session，并在后续 Developer Turn 中恢复它。
- 两个角色都使用明确选择的 `default` Launch Profile。

## Shared context policy

每轮读取 `REQUEST.md`、本协议、当前 `input.md`、当前工作区和独立 System Facts。
Kickoff、Handoff 与 Resume Payload 都是冻结的直接输入。只传递正式 Handoff，不
传递私有思维链；接收方必须独立复核发送方事实与判断。

## Block and resume policy

所有 Block 必须返回用户。只有可 Resume Block 在新的明确用户指令到达后，才能由
Origin 管理 Claim 选择目标 Role 并 Resume。Limit 与 Profile Changed Block 必须
取消旧 Run 后创建新 Run。`recover` 只做确定性技术收口，不代表用户授权 Resume。

## Assumptions made during bootstrap

- “P3 和 P3 以上”解释为 P0、P1、P2、P3 均阻塞，P4 不阻塞。
- 用户要求验证 accept/reject 与 re-review 分支，因此第二个 Run 以 Reviewer 开始。
- Request 中的 bytecode/Facts 差异来自上一真实 Run 的冻结证据，不是人为注入。
- 用户未指定新增功能；除解决直接审计问题外，不扩展产品范围。
- reviewer 与 developer 都使用 External Binding，当前 Origin Session 只负责
  Bootstrap、等待和最终审计。

## Safety limits

最多 12 个业务 Turn，Wall Time 为 3600 秒。所有角色串行共享同一 Git worktree；
运行期间不手工并发编辑。不得启动逃逸 Runner PGID 的后台 daemon，不得跟踪
`.agent-team/`，不得启用 Sparse Checkout或新增 Gitlink。达到上限后使用新 Run，
不得在线改写配置。
