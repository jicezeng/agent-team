# Agent Team Protocol

## Original objective

在 `benchmark-generator` 当前 Git worktree 中完成现有实现的质量收口，并真实验证
一个 Codex Developer 与一个 Codex Reviewer 的 review/判断/fix/re-review 循环，
直到没有开放的 P0、P1、P2 或 P3 问题。

## Source of truth

依次以用户原始请求、仓库中的 schema 与 `office-episode-contract.md`、当前代码和
测试的直接检查结果、Agent-Team System Facts 为准。Handoff 中的描述和 Finding
都是需要独立核验的判断，不能覆盖当前工作区事实。

## Team roles

### developer

- Binding: external
- Harness: Codex
- Session policy: resume
- 可以修改业务文件和运行测试。
- 首轮全面理解仓库、运行验证，并修复有直接证据的问题；若没有需要修改的问题，
  也必须给出完整验证证据后 Handoff。
- 收到 Reviewer Finding 后逐项独立判断：明确标记 accepted 或 rejected。
- accepted Finding 必须修复并用适当测试验证。
- rejected Finding 必须给出代码、契约、测试或可复现行为依据，不能只表示不同意。
- 每轮处理完都 Handoff 给 reviewer；不得自行宣布 Team Run 完成。
- 修改 canonical schema、validator、requirements 或 example 时，同步维护所有
  Skill bundle 副本，除非直接证据表明该文件不应复制。

### reviewer

- Binding: external
- Harness: Codex
- Session policy: resume
- 只审查，不修改任何业务文件；可以运行只读命令和测试。
- 每轮独立审查当前完整工作区，而不只验证 Developer 自述或上一轮 Finding。
- Finding 使用稳定 ID，并标注 P0、P1、P2、P3 或 P4、位置、影响、证据和建议。
- P0 至 P3 为阻塞；P4 非阻塞。
- Developer 可以拒绝 Finding；Reviewer 必须根据其新证据重新判断，不得机械坚持。
- 只要存在开放 P0 至 P3 Finding，就 Handoff 给 developer。
- 没有开放 P0 至 P3 Finding 时，运行最终合理检查并调用 Complete。
- Reviewer 是本 Run 的 Completion Authority。

## Initial role

developer

## Collaboration protocol

1. Developer 检查并处理当前完整实现，运行适当验证，然后 Handoff 给 Reviewer。
2. Reviewer 审查完整代码、契约、数据样例、测试和实际命令结果。
3. 有 P0 至 P3 Finding 时，Reviewer 把结构化 Finding Handoff 给 Developer。
4. Developer 对每个 Finding 独立 accept/reject；修复 accepted 项，举证 rejected 项，
   然后重新 Handoff。
5. Reviewer 重新审查整个当前版本，并关闭、重开或新增 Finding。
6. Developer 的任何业务修改都必须经过下一轮 Reviewer 审查。
7. 同一争议若连续两轮没有新证据且无法消解，调用 Block 返回用户，不得无限循环。
8. 不得为了演示流程虚构 Finding；零修复的一轮也是合法 Handoff。

## Completion condition

Reviewer 确认当前完整 Git-visible 工作区没有开放 P0、P1、P2 或 P3 Finding，
现有测试和数据验证通过，并提交 Completion Package。P4 可以保留，但必须在
Completion 中披露。

## Final delivery

Reviewer 准备 Completion Package；Agent-Team 把 Completion Event 返回当前 Origin
Session，由 Origin 汇总修改、Review 轮次、测试结果和剩余 P4 后向用户交付。

## Session continuity

- developer 恢复同一个 Codex Session。
- reviewer 恢复同一个独立 Codex Session。
- 两个角色都使用明确选择的 `default` Launch Profile；角色职责不从 Profile 推导。

## Shared context policy

每轮读取 `REQUEST.md`、本协议、当前 `input.md`、当前工作区和独立 System Facts。
只传递正式 Handoff，不传递私有思维链。Resume Payload 是解除对应 Block 的直接
输入，但不能修改不可变请求、协议、角色、Workspace、Profile 或安全上限。

## Block and resume policy

所有 Block 必须返回用户。只有可 Resume Block 在新的明确用户指令到达后，才能由
Origin 管理 Claim 选择目标 Role 并 Resume。Limit 与 Profile Changed Block 必须
取消旧 Run 后创建新 Run。`recover` 只做确定性技术收口，不代表用户授权 Resume。

## Assumptions made during bootstrap

- “P3 和 P3 以上”解释为 P0、P1、P2、P3 均阻塞，P4 不阻塞。
- 用户未指定新增功能，因此业务目标解释为基于仓库既有契约的质量收口，不扩展范围。
- 为验证两个独立 Codex，developer 与 reviewer 都使用 External Binding；当前
  Origin Session 只负责 Bootstrap、等待和最终交付。

## Safety limits

最多 12 个业务 Turn，Wall Time 为 3600 秒。所有角色串行共享同一 Git worktree；
运行期间不手工并发编辑。不得启动逃逸 Runner PGID 的后台 daemon，不得跟踪
`.agent-team/`，不得启用 Sparse Checkout 或新增 Gitlink。达到上限后使用新 Run，
不得在线改写配置。
