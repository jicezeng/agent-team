# Request

用户原始指令：

> 继续，取消当前 Block Run，修复 Codex Interactive 私有 config.toml 未预置 tui.model_availability_nux、导致首 Turn 自写后被误判为 profile_changed 的问题；按冻结角色模型预置并严格校验该状态，补测后重新执行四 Harness full-access 回归，直至通过并提交修改。

在独立 Git 工作区执行一次真实四 Harness relay 回归。Codex、Claude Code、OpenCode、DeepSeek Harness 必须各自真实运行并通过正式 Agent-Team Handoff 协作。只允许修改 `relay.md`，按顺序形成 `CODEX-1`、`CLAUDE`、`OPENCODE`、`DSH`、`CODEX-2` 五个 Marker。最终 Codex 必须以同一个 Session 恢复，验证顺序与工作区边界后完成 Run。
