from __future__ import annotations

from pathlib import Path

from .adapters import get_adapter
from .journal import scan_journal
from .processes import process_group_exists, process_identity_state
from .state import read_owner, release_owner
from .turns import iter_runtimes


def release_terminal_owner_locked(run_dir: Path) -> bool:
    """Release an exact terminal Owner only after fresh process checks.

    The caller must hold the Workspace operation lock and the Run lock in that
    order. ``locked_run`` provides that lock scope for normal lifecycle paths.
    """
    projection = scan_journal(run_dir)
    if projection.status not in {"COMPLETED", "CANCELLED"}:
        return False
    runtimes = iter_runtimes(run_dir, team=projection.team)
    for runtime in runtimes:
        if runtime["phase"] != "finalized":
            return False
        if runtime["executor"] != "worker":
            continue
        if runtime["group_quiescent"] is not True:
            return False
        supervisor_pid = runtime["supervisor_pid"]
        if supervisor_pid is not None and process_identity_state(
            supervisor_pid,
            runtime["supervisor_start_id"],
        ) not in {"gone", "reused"}:
            return False
        runner_pgid = runtime["runner_pgid"]
        if runner_pgid is not None:
            if process_group_exists(runner_pgid):
                return False
            if process_identity_state(
                runtime["runner_pid"],
                runtime["runner_start_id"],
                pgid=runner_pgid,
            ) not in {"gone", "reused"}:
                return False
    # ``start`` prepares private state for every External role before Kickoff.
    # A terminal Run may never route to some of those roles, so their adapters
    # have no Turn finalizer that could remove transient authentication/probe
    # state. Only after every Runtime above is finalized and process-quiescent
    # may terminal cleanup close every prepared External role.
    for role in projection.team.roles.values():
        if role.binding != "external":
            continue
        get_adapter(role.adapter or "").finalize_run_state(
            run_dir=run_dir,
            role_id=role.role_id,
            launch_mode=role.launch_mode or "headless",
        )
    owner = read_owner(projection.team.workspace)
    if owner is None:
        return True
    if owner["run_id"] != projection.team.run_id:
        # A historical terminal Run may be inspected after a newer Run has
        # legitimately acquired the Workspace.
        return False
    return release_owner(projection.team.workspace, projection.team.run_id)
