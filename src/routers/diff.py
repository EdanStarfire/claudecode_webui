"""Diff endpoints: /api/sessions/{session_id}/diff*"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sessions/{session_id}/diff")
    @handle_exceptions("get session diff")
    async def get_session_diff(session_id: str):
        """Get diff summary for a session's working directory vs origin/main."""
        ctx = await webui.service.get_session_diff_context(session_id)
        if not ctx.get("exists"):
            raise HTTPException(status_code=404, detail="Session not found")

        cwd = ctx.get("working_directory")
        if not cwd or not Path(cwd).exists():
            return {"is_git_repo": False}

        # Check if it's a git repo
        is_git = await webui._run_git_command(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd
        )
        if is_git is None:
            return {"is_git_repo": False}

        # Get current branch
        branch = await webui._run_git_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd
        )

        # Find merge base with origin/main (fallback to origin/master)
        merge_base = await webui._run_git_command(
            ["git", "merge-base", "HEAD", "origin/main"], cwd
        )
        if merge_base is None:
            merge_base = await webui._run_git_command(
                ["git", "merge-base", "HEAD", "origin/master"], cwd
            )
        # Track whether we're in local-only mode (no remote)
        is_local_only = merge_base is None
        if is_local_only:
            # No remote: use the empty tree as base so all commits/files are shown
            empty_tree = await webui._run_git_command(
                ["git", "hash-object", "-t", "tree", "/dev/null"], cwd
            )
            if empty_tree:
                merge_base = empty_tree.strip()
            else:
                # Fallback to well-known empty tree hash
                merge_base = "4b825dc642cb6eb9a060e54bf899d15f7f09f993"

        # Get commit log since merge base
        if is_local_only:
            # Local-only: show all commits (--root includes initial commit)
            log_output = await webui._run_git_command(
                ["git", "log", "--format=%H%n%h%n%s%n%an%n%aI%n---COMMIT_END---"],
                cwd
            )
        else:
            log_output = await webui._run_git_command(
                ["git", "log", f"{merge_base}..HEAD",
                 "--format=%H%n%h%n%s%n%an%n%aI%n---COMMIT_END---"],
                cwd
            )
        commits = []
        if log_output:
            raw_commits = log_output.strip().split("---COMMIT_END---")
            for raw in raw_commits:
                lines = raw.strip().split("\n")
                if len(lines) >= 5:
                    # Get files for this commit
                    commit_files_output = await webui._run_git_command(
                        ["git", "diff-tree", "--no-commit-id", "--root",
                         "-r", "--name-only", lines[0]], cwd
                    )
                    commit_files = [
                        f for f in (commit_files_output or "").strip().split("\n")
                        if f
                    ]
                    commits.append({
                        "hash": lines[0],
                        "short_hash": lines[1],
                        "message": lines[2],
                        "author": lines[3],
                        "date": lines[4],
                        "files": commit_files
                    })

        # Detect uncommitted changes (staged + unstaged + untracked)
        status_output = await webui._run_git_command(
            ["git", "status", "--porcelain"], cwd
        )
        uncommitted_files = []
        untracked_paths = []
        if status_output:
            for line in status_output.strip().split("\n"):
                if not line or len(line) < 3:
                    continue
                xy = line[:2]
                path = line[3:].strip()
                # Handle renames: "R  old -> new"
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                if xy == "??":
                    untracked_paths.append(path)
                else:
                    uncommitted_files.append(path)

        # Build synthetic uncommitted commit if dirty working tree
        if uncommitted_files or untracked_paths:
            # Tracked file stats: combined staged+unstaged vs HEAD
            wip_numstat = await webui._run_git_command(
                ["git", "diff", "--numstat", "HEAD"], cwd
            )
            wip_name_status = await webui._run_git_command(
                ["git", "diff", "--name-status", "HEAD"], cwd
            )
            # Also include staged new files (added but not in HEAD)
            staged_numstat = await webui._run_git_command(
                ["git", "diff", "--numstat", "--cached"], cwd
            )
            staged_name_status = await webui._run_git_command(
                ["git", "diff", "--name-status", "--cached"], cwd
            )

            wip_status_map = {}
            if wip_name_status:
                for line in wip_name_status.strip().split("\n"):
                    if line:
                        parts = line.split("\t", 1)
                        if len(parts) == 2:
                            sc = parts[0].strip()
                            fp = parts[1].strip()
                            if sc.startswith("A"):
                                wip_status_map[fp] = "added"
                            elif sc.startswith("D"):
                                wip_status_map[fp] = "deleted"
                            elif sc.startswith("R"):
                                wip_status_map[fp] = "renamed"
                            else:
                                wip_status_map[fp] = "modified"
            # Merge staged-only entries (new files that only show in --cached)
            if staged_name_status:
                for line in staged_name_status.strip().split("\n"):
                    if line:
                        parts = line.split("\t", 1)
                        if len(parts) == 2:
                            sc = parts[0].strip()
                            fp = parts[1].strip()
                            if fp not in wip_status_map:
                                if sc.startswith("A"):
                                    wip_status_map[fp] = "added"
                                elif sc.startswith("D"):
                                    wip_status_map[fp] = "deleted"
                                else:
                                    wip_status_map[fp] = "modified"

            wip_files_list = []
            # Parse tracked file numstat
            all_numstat = (wip_numstat or "")
            if staged_numstat:
                # Merge staged numstat for files not in wip_numstat
                seen = set()
                if all_numstat:
                    for line in all_numstat.strip().split("\n"):
                        if line:
                            p = line.split("\t")
                            if len(p) >= 3:
                                seen.add(p[2].strip())
                for line in staged_numstat.strip().split("\n"):
                    if line:
                        p = line.split("\t")
                        if len(p) >= 3 and p[2].strip() not in seen:
                            all_numstat += "\n" + line

            if all_numstat:
                for line in all_numstat.strip().split("\n"):
                    if line:
                        parts = line.split("\t")
                        if len(parts) >= 3:
                            fp = parts[2].strip()
                            wip_files_list.append(fp)

            # Add untracked files
            for upath in untracked_paths:
                wip_files_list.append(upath)
                wip_status_map[upath] = "added"

            synthetic_commit = {
                "hash": "uncommitted",
                "short_hash": "wip",
                "message": "Uncommitted changes",
                "author": "",
                "date": "",
                "files": wip_files_list,
                "is_uncommitted": True
            }
            commits.insert(0, synthetic_commit)

        # Total stats: two-dot notation includes uncommitted changes
        numstat_output = await webui._run_git_command(
            ["git", "diff", "--numstat", merge_base], cwd
        )
        name_status_output = await webui._run_git_command(
            ["git", "diff", "--name-status", merge_base], cwd
        )

        files = {}
        total_insertions = 0
        total_deletions = 0

        # Parse name-status for A/M/D
        status_map = {}
        if name_status_output:
            for line in name_status_output.strip().split("\n"):
                if line:
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        status_code = parts[0].strip()
                        filepath = parts[1].strip()
                        if status_code.startswith("A"):
                            status_map[filepath] = "added"
                        elif status_code.startswith("D"):
                            status_map[filepath] = "deleted"
                        elif status_code.startswith("R"):
                            status_map[filepath] = "renamed"
                        else:
                            status_map[filepath] = "modified"

        # Parse numstat for insertions/deletions
        if numstat_output:
            for line in numstat_output.strip().split("\n"):
                if line:
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        ins = parts[0].strip()
                        dels = parts[1].strip()
                        filepath = parts[2].strip()
                        ins_count = int(ins) if ins != "-" else 0
                        dels_count = int(dels) if dels != "-" else 0
                        is_binary = ins == "-" and dels == "-"
                        total_insertions += ins_count
                        total_deletions += dels_count
                        files[filepath] = {
                            "status": status_map.get(filepath, "modified"),
                            "insertions": ins_count,
                            "deletions": dels_count,
                            "is_binary": is_binary
                        }

        # Add untracked files to total stats (not covered by git diff)
        if untracked_paths:
            for upath in untracked_paths:
                if upath not in files:
                    ustat = await webui._run_git_command(
                        ["git", "diff", "--numstat", "--no-index",
                         "/dev/null", upath],
                        cwd, allow_nonzero=True
                    )
                    ins_count = 0
                    if ustat:
                        parts = ustat.split("\t")
                        if len(parts) >= 3:
                            ins_val = parts[0].strip()
                            ins_count = int(ins_val) if ins_val != "-" else 0
                    total_insertions += ins_count
                    files[upath] = {
                        "status": "added",
                        "insertions": ins_count,
                        "deletions": 0,
                        "is_binary": False
                    }

        return {
            "is_git_repo": True,
            "merge_base": merge_base,
            "branch": branch or "unknown",
            "commits": commits,
            "files": files,
            "total_insertions": total_insertions,
            "total_deletions": total_deletions
        }

    @router.get("/api/sessions/{session_id}/diff/file")
    @handle_exceptions("get session diff file")
    async def get_session_diff_file(
        session_id: str, path: str = None, ref: str = None
    ):
        """Get unified diff content for a specific file.

        Args:
            ref: Optional. ``uncommitted`` for working tree changes,
                a commit hash for commit-specific changes, or null/empty
                for cumulative branch diff (merge_base...HEAD).
        """
        if not path:
            raise HTTPException(status_code=400, detail="path query parameter required")

        ctx = await webui.service.get_session_diff_context(session_id)
        if not ctx.get("exists"):
            raise HTTPException(status_code=404, detail="Session not found")

        cwd = ctx.get("working_directory")
        if not cwd or not Path(cwd).exists():
            raise HTTPException(status_code=400, detail="Invalid working directory")

        if ref and ref != "uncommitted":
            # Commit-specific diff: validate ref then diff against parent
            verified = await webui._run_git_command(
                ["git", "rev-parse", "--verify", ref], cwd
            )
            if verified is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid commit reference: {ref}"
                )

            # Check if this commit has a parent
            parent = await webui._run_git_command(
                ["git", "rev-parse", "--verify", f"{ref}~1"], cwd
            )
            if parent:
                # Normal commit: diff against parent
                diff_output = await webui._run_git_command(
                    ["git", "diff", f"{ref}~1", ref, "--", path], cwd
                )
            else:
                # Root commit: diff against empty tree
                empty_tree = await webui._run_git_command(
                    ["git", "hash-object", "-t", "tree", "/dev/null"], cwd
                )
                base = (empty_tree.strip() if empty_tree
                        else "4b825dc642cb6eb9a060e54bf899d15f7f09f993")
                diff_output = await webui._run_git_command(
                    ["git", "diff", base, ref, "--", path], cwd
                )

            return {
                "path": path,
                "ref": ref,
                "diff": diff_output or ""
            }

        # Find merge base for uncommitted / total views
        merge_base = await webui._run_git_command(
            ["git", "merge-base", "HEAD", "origin/main"], cwd
        )
        if merge_base is None:
            merge_base = await webui._run_git_command(
                ["git", "merge-base", "HEAD", "origin/master"], cwd
            )

        if ref == "uncommitted":
            # Check if file is untracked
            is_tracked = await webui._run_git_command(
                ["git", "ls-files", path], cwd
            )
            status_check = await webui._run_git_command(
                ["git", "status", "--porcelain", "--", path], cwd
            )
            is_untracked = (
                status_check and status_check.startswith("??")
            )

            if is_untracked or not is_tracked:
                # Untracked file: diff vs /dev/null
                diff_output = await webui._run_git_command(
                    ["git", "diff", "--no-index", "/dev/null", path],
                    cwd, allow_nonzero=True
                )
            else:
                # Tracked file: diff against merge base, or HEAD if no remote
                base = merge_base or "HEAD"
                diff_output = await webui._run_git_command(
                    ["git", "diff", base, "--", path], cwd
                )
        elif merge_base is not None:
            # Default: three-dot (committed changes only)
            diff_output = await webui._run_git_command(
                ["git", "diff", f"{merge_base}...HEAD", "--", path], cwd
            )
        else:
            # No remote: diff all changes from empty tree
            empty_tree = await webui._run_git_command(
                ["git", "hash-object", "-t", "tree", "/dev/null"], cwd
            )
            base = (empty_tree.strip() if empty_tree
                    else "4b825dc642cb6eb9a060e54bf899d15f7f09f993")
            diff_output = await webui._run_git_command(
                ["git", "diff", base, "HEAD", "--", path], cwd
            )

        return {
            "path": path,
            "merge_base": merge_base,
            "diff": diff_output or ""
        }

    return router
