"""Deterministic synthetic ``messages.jsonl`` generator for the history-index load test
(issue #2030 stage 2 PR A, §3.5).

Produces realistic mixed records — user turns, assistant text, tool_call records (mostly a few
KB, a few 100-500 KB results), tool-result carrier user rows, hook/thinking_tokens/status system
rows, Task* tool calls, background-agent lifecycle frames and periodic compaction groups — scaled
to an exact record count and an approximate byte size, so the three owner-relevant shapes can be
generated: ~2,000 records / 19 MB, ~20,000 / 22 MB and ~90,000 / 113 MB.
"""

import json
import random
import uuid
from pathlib import Path
from typing import Any

PRESETS = {
    "small": {"records": 2_000, "bytes": 19 * 1024 * 1024},
    "medium": {"records": 20_000, "bytes": 22 * 1024 * 1024},
    "huge": {"records": 90_000, "bytes": 113 * 1024 * 1024},
}

CONTINUATION = "This session is being continued from a previous conversation that ran out of context."
_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron".split()


def _text(rng: random.Random, n: int) -> str:
    if n <= 0:
        return ""
    chunk = " ".join(rng.choice(_WORDS) for _ in range(64)) + "\n"
    return (chunk * (n // len(chunk) + 1))[:n]


def generate_session_file(
    path: Path,
    *,
    records: int,
    target_bytes: int,
    session_id: str,
    seed: int = 1,
    big_results: int | None = None,
    rare_token: str | None = None,
    tool_results_metadata: bool = False,
) -> dict[str, Any]:
    """Write ``records`` JSONL lines to ``path`` (approximately ``target_bytes``).

    ``tool_results_metadata=True`` adds ``metadata.tool_results=[{tool_use_id}]`` to the tool-result
    carrier rows the way real sessions have it (the stock rows omit it, which leaves the browser's
    hook-correlation pending-tool queue growing without bound — a fixture artifact). Default False
    keeps the output byte-identical for the stage 2/3 load tests.

    Returns ``{"records", "bytes", "turns", "compactions"}`` (the true turn / group counts, from
    construction, independent of the reducer)."""
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    big_budget = big_results if big_results is not None else max(2, records // 400)
    est_tool_calls = max(1, records // 3)
    big_set = set(rng.sample(range(1, est_tool_calls), min(est_tool_calls - 1, big_budget)))
    sizes: dict[int, int] = {i: rng.randint(100_000, 500_000) for i in big_set}
    # Byte size is ~linear in the average tool-result size: fit it from two cheap probe runs
    # (the generator is fast), then regenerate at the solved value.
    low, high = 50.0, 2000.0
    base = _write(path, rng_seed=seed, records=records, session_id=session_id, sizes=sizes,
                  avg_tool=low, rare_token=rare_token,
                  tool_results_metadata=tool_results_metadata)["bytes"]
    probe = _write(path, rng_seed=seed, records=records, session_id=session_id, sizes=sizes,
                   avg_tool=high, rare_token=rare_token,
                   tool_results_metadata=tool_results_metadata)["bytes"]
    slope = max(1e-9, (probe - base) / (high - low))
    avg_tool = max(low, low + (target_bytes - base) / slope)
    return _write(path, rng_seed=seed, records=records, session_id=session_id, sizes=sizes,
                  avg_tool=avg_tool, rare_token=rare_token,
                   tool_results_metadata=tool_results_metadata)


def _write(path: Path, *, rng_seed: int, records: int, session_id: str, sizes: dict[int, int],
           avg_tool: float, rare_token: str | None = None,
           tool_results_metadata: bool = False) -> dict[str, Any]:
    rng = random.Random(rng_seed)
    ts = 1_700_000_000.0

    turns = compactions = 0
    written = 0
    n = 0
    tool_n = 0
    rare_user = rare_result = False  # `rare_token` appears exactly once in a user turn (~25 %)
    with open(path, "w", encoding="utf-8") as f:  # and once in a tool result (~50 % of the file)

        def emit(rec: dict[str, Any]) -> None:
            nonlocal ts, written, n
            ts += 0.5
            rec.setdefault("message_id", str(uuid.UUID(int=rng.getrandbits(128), version=4)))
            rec["timestamp"] = ts
            rec["session_id"] = session_id
            line = json.dumps(rec, ensure_ascii=False) + "\n"
            f.write(line)
            written += len(line.encode("utf-8"))
            n += 1

        emit({"type": "system", "content": "System message", "subtype": "init",
              "metadata": {"subtype": "init", "init_data": {"tools": ["Read", "Bash"], "model": "m"}}})
        while n < records:
            if turns % 40 == 0 and turns:  # a session restart / resume re-emits init
                emit({"type": "system", "content": "System message", "subtype": "init",
                      "metadata": {"subtype": "init",
                                   "init_data": {"tools": ["Read", "Bash"], "model": "m"}}})
            user_text = f"turn {turns}: " + _text(rng, rng.randint(20, 200))
            if rare_token and not rare_user and n >= records // 4:
                rare_user, user_text = True, user_text + f" {rare_token}-user"
            emit({"type": "user", "content": user_text})
            turns += 1
            for _ in range(rng.randint(1, 6)):
                if n >= records:
                    break
                emit({"type": "assistant", "content": _text(rng, rng.randint(100, 1200)),
                      "metadata": {"tool_uses": [{"id": f"tu{tool_n + 1}", "name": "Bash"}]}})
                if n >= records:
                    break
                tool_n += 1
                size = sizes.get(tool_n)
                if size is None:
                    size = int(rng.lognormvariate(0, 0.9) * avg_tool)
                name = rng.choice(["Bash", "Read", "Grep", "Edit"])
                if tool_n % 40 == 0:
                    name = "TaskCreate"
                    size = 60
                result_text = f"Task #{tool_n} created" if name == "TaskCreate" else _text(rng, size)
                if rare_token and not rare_result and n >= records // 2 and name != "TaskCreate":
                    rare_result, result_text = True, result_text + f" {rare_token}-result"
                emit({"type": "tool_call", "tool_use_id": f"tu{tool_n}", "name": name,
                      "status": "completed", "input": {"subject": f"t{tool_n}"} if name == "TaskCreate"
                      else {"command": "ls"}, "result": result_text})
                if n >= records:
                    break
                carrier_meta: dict[str, Any] = {"has_tool_results": True}
                if tool_results_metadata:
                    carrier_meta["tool_results"] = [{"tool_use_id": f"tu{tool_n}"}]
                emit({"type": "user", "content": "Tool results: 1 result", "metadata": carrier_meta})
                if rng.random() < 0.05 and n < records:
                    emit({"type": "system", "content": "", "metadata": {"subtype": "thinking_tokens"}})
                if rng.random() < 0.02 and n + 1 < records:
                    emit({"type": "system", "content": "", "metadata": {
                        "subtype": "task_started", "task_id": f"bg{n}"}})
            if turns % 150 == 0 and n + 4 < records:
                for status in ("compacting", None):
                    emit({"type": "system", "content": "System message",
                          "metadata": {"subtype": "status", "init_data": {"status": status}}})
                emit({"type": "system", "content": "compacted",
                      "metadata": {"subtype": "compact_boundary"}})
                emit({"type": "user", "content": CONTINUATION})
                compactions += 1
    return {"records": n, "bytes": path.stat().st_size, "turns": turns, "compactions": compactions}
