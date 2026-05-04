import asyncio
import time
import uuid
from threading import RLock
from typing import Any, Dict, List, Optional

MAX_RUNS = 200


class TraceRuntime:
    def __init__(self) -> None:
        self._lock = RLock()
        self._events_by_run: Dict[str, List[Dict[str, Any]]] = {}
        self._next_seq_by_run: Dict[str, int] = {}
        self._run_summary: Dict[str, Dict[str, Any]] = {}
        self._subscribers_by_run: Dict[str, List[asyncio.Queue]] = {}
        self._runs_by_session: Dict[int, List[str]] = {}
        self._run_order: List[str] = []

    def start_run(
        self,
        *,
        session_id: int,
        user_id: int,
        mode: str,
        trigger_type: str = "message",
        trigger_message_id: Optional[int] = None,
        alert_event_id: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._events_by_run[run_id] = []
            self._next_seq_by_run[run_id] = 1
            self._subscribers_by_run[run_id] = []
            self._run_summary[run_id] = {
                "run_id": run_id,
                "session_id": session_id,
                "user_id": user_id,
                "mode": mode,
                "trigger_type": trigger_type,
                "trigger_message_id": trigger_message_id,
                "alert_event_id": alert_event_id,
                "thread_id": thread_id,
                "status": "running",
                "start_ts": now,
                "end_ts": None,
                "duration_ms": None,
                "done": False,
                "error_summary": "",
                "token_summary": {
                    "prompt_tokens_total": 0,
                    "completion_tokens_total": 0,
                    "total_tokens": 0,
                    "llm_calls_count": 0,
                },
            }
            self._runs_by_session.setdefault(session_id, []).append(run_id)
            self._run_order.append(run_id)
            self._trim_runs_locked()

        return run_id

    def _trim_runs_locked(self) -> None:
        while len(self._run_order) > MAX_RUNS:
            oldest = self._run_order.pop(0)
            self._events_by_run.pop(oldest, None)
            self._next_seq_by_run.pop(oldest, None)
            summary = self._run_summary.pop(oldest, None)
            if summary is not None:
                session_id = int(summary.get("session_id") or 0)
                run_ids = self._runs_by_session.get(session_id, [])
                self._runs_by_session[session_id] = [run_id for run_id in run_ids if run_id != oldest]
                if not self._runs_by_session[session_id]:
                    self._runs_by_session.pop(session_id, None)
            subscribers = self._subscribers_by_run.pop(oldest, [])
            for q in subscribers:
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

    def append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        call_id: str,
        status: str,
        meta: Dict[str, Any],
        duration_ms: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            events = self._events_by_run.get(run_id)
            if events is None:
                return None
            seq = self._next_seq_by_run.get(run_id, 1)
            event = {
                "run_id": run_id,
                "seq": seq,
                "ts": now,
                "type": event_type,
                "call_id": call_id,
                "status": status,
                "duration_ms": duration_ms,
                "meta": meta,
            }
            events.append(event)
            self._next_seq_by_run[run_id] = seq + 1
            subscribers = list(self._subscribers_by_run.get(run_id, []))

        for q in subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                continue

        return event

    def add_usage(self, run_id: str, usage: Dict[str, Any]) -> None:
        with self._lock:
            summary = self._run_summary.get(run_id)
            if summary is None:
                return
            token_summary = summary.get("token_summary", {})

            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

            token_summary["prompt_tokens_total"] = int(token_summary.get("prompt_tokens_total") or 0) + prompt_tokens
            token_summary["completion_tokens_total"] = int(token_summary.get("completion_tokens_total") or 0) + completion_tokens
            token_summary["total_tokens"] = int(token_summary.get("total_tokens") or 0) + total_tokens
            token_summary["llm_calls_count"] = int(token_summary.get("llm_calls_count") or 0) + 1

    def end_run(self, run_id: str, *, status: str, error_summary: str = "") -> None:
        now = time.time()
        with self._lock:
            summary = self._run_summary.get(run_id)
            if summary is None:
                return
            start_ts = float(summary.get("start_ts") or now)
            summary["status"] = status
            summary["end_ts"] = now
            summary["duration_ms"] = max(0, int((now - start_ts) * 1000))
            summary["done"] = True
            summary["error_summary"] = error_summary
            subscribers = list(self._subscribers_by_run.get(run_id, []))

        terminal = {
            "run_id": run_id,
            "status": status,
            "error_summary": error_summary,
        }
        for q in subscribers:
            try:
                q.put_nowait({"type": "run_end", "data": terminal})
            except Exception:
                continue

    def get_events(self, run_id: str, since_seq: int = 0) -> Dict[str, Any]:
        with self._lock:
            events = self._events_by_run.get(run_id)
            summary = self._run_summary.get(run_id)
            if events is None or summary is None:
                return {"exists": False}
            out = [e for e in events if int(e.get("seq") or 0) > since_seq]
            latest_seq = int(self._next_seq_by_run.get(run_id, 1) - 1)
            done = bool(summary.get("done"))
        return {
            "exists": True,
            "events": out,
            "latest_seq": latest_seq,
            "done": done,
        }

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            summary = self._run_summary.get(run_id)
            if summary is None:
                return None
            return dict(summary)

    def list_runs(
        self,
        *,
        user_id: int,
        visible_user_ids: Optional[List[int]] = None,
        session_id: Optional[int] = None,
        status: Optional[str] = None,
        mode: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        with self._lock:
            if session_id is None:
                run_ids = list(reversed(self._run_order))
            else:
                run_ids = list(reversed(self._runs_by_session.get(session_id, [])))

            runs = []
            for run_id in run_ids:
                summary = self._run_summary.get(run_id)
                if summary is None:
                    continue
                if not self._can_read_summary_locked(summary, user_id=user_id, visible_user_ids=visible_user_ids):
                    continue
                if status and summary.get("status") != status:
                    continue
                if mode and summary.get("mode") != mode:
                    continue
                runs.append(dict(summary))

            total = len(runs)
            end = max(skip, 0) + max(limit, 0)
            return {"runs": runs[max(skip, 0):end], "total": total}

    def get_session_summary(self, *, session_id: int, user_id: int) -> Dict[str, Any]:
        with self._lock:
            return self._build_session_summary_locked(session_id=session_id, user_id=user_id)

    def list_session_summaries(
        self,
        *,
        user_id: int,
        visible_user_ids: Optional[List[int]] = None,
        status: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            matched_session_ids = []
            seen_session_ids = set()

            for run_id in reversed(self._run_order):
                summary = self._run_summary.get(run_id)
                if summary is None:
                    continue
                if not self._can_read_summary_locked(summary, user_id=user_id, visible_user_ids=visible_user_ids):
                    continue
                if status and summary.get("status") != status:
                    continue
                if mode and summary.get("mode") != mode:
                    continue

                session_id = int(summary.get("session_id") or 0)
                if session_id in seen_session_ids:
                    continue
                seen_session_ids.add(session_id)
                matched_session_ids.append(session_id)

            return [
                self._build_session_summary_locked(
                    session_id=session_id,
                    user_id=int(self._run_summary[self._runs_by_session[session_id][-1]].get("user_id") or user_id),
                )
                for session_id in matched_session_ids
            ]

    def _build_session_summary_locked(self, *, session_id: int, user_id: int) -> Dict[str, Any]:
        run_ids = self._runs_by_session.get(session_id, [])
        summaries = [
            self._run_summary[run_id]
            for run_id in run_ids
            if run_id in self._run_summary
            and int(self._run_summary[run_id].get("user_id") or -1) == int(user_id)
        ]

        status_counts: Dict[str, int] = {}
        token_summary = {
            "prompt_tokens_total": 0,
            "completion_tokens_total": 0,
            "total_tokens": 0,
            "llm_calls_count": 0,
        }
        duration_ms_total = 0
        running_count = 0

        for summary in summaries:
            status_value = str(summary.get("status") or "unknown")
            status_counts[status_value] = status_counts.get(status_value, 0) + 1
            if not bool(summary.get("done")):
                running_count += 1
            duration_ms_total += int(summary.get("duration_ms") or 0)

            run_tokens = summary.get("token_summary") or {}
            for key in token_summary:
                token_summary[key] += int(run_tokens.get(key) or 0)

        return {
            "session_id": session_id,
            "run_count": len(summaries),
            "running_count": running_count,
            "status_counts": status_counts,
            "duration_ms_total": duration_ms_total,
            "token_summary": token_summary,
            "latest_run_id": summaries[-1]["run_id"] if summaries else None,
            "latest_start_ts": summaries[-1]["start_ts"] if summaries else None,
            "latest_end_ts": summaries[-1]["end_ts"] if summaries else None,
        }

    def get_session_events(
        self,
        *,
        session_id: int,
        user_id: int,
        since_ts: float = 0,
        limit: int = 500,
    ) -> Dict[str, Any]:
        with self._lock:
            run_ids = self._runs_by_session.get(session_id, [])
            events = []
            latest_ts = since_ts
            latest_seq_by_run: Dict[str, int] = {}

            for run_id in run_ids:
                summary = self._run_summary.get(run_id)
                if summary is None or int(summary.get("user_id") or -1) != int(user_id):
                    continue
                latest_seq_by_run[run_id] = int(self._next_seq_by_run.get(run_id, 1) - 1)
                for event in self._events_by_run.get(run_id, []):
                    event_ts = float(event.get("ts") or 0)
                    latest_ts = max(latest_ts, event_ts)
                    if event_ts > since_ts:
                        events.append(dict(event))

            events.sort(
                key=lambda item: (
                    float(item.get("ts") or 0),
                    str(item.get("run_id") or ""),
                    int(item.get("seq") or 0),
                )
            )
            if limit >= 0:
                events = events[-limit:]

            return {
                "exists": bool(run_ids),
                "events": events,
                "latest_ts": latest_ts,
                "latest_seq_by_run": latest_seq_by_run,
                "done": all(bool(self._run_summary.get(run_id, {}).get("done")) for run_id in run_ids),
            }

    def _can_read_summary_locked(
        self,
        summary: Dict[str, Any],
        *,
        user_id: int,
        visible_user_ids: Optional[List[int]] = None,
    ) -> bool:
        allowed_user_ids = {int(user_id)}
        if visible_user_ids:
            allowed_user_ids.update(int(item) for item in visible_user_ids)
        return int(summary.get("user_id") or -1) in allowed_user_ids

    def check_owner(self, run_id: str, user_id: int, visible_user_ids: Optional[List[int]] = None) -> bool:
        with self._lock:
            summary = self._run_summary.get(run_id)
            if summary is None:
                return False
            return self._can_read_summary_locked(summary, user_id=user_id, visible_user_ids=visible_user_ids)

    def subscribe(self, run_id: str) -> Optional[asyncio.Queue]:
        with self._lock:
            if run_id not in self._run_summary:
                return None
            q: asyncio.Queue = asyncio.Queue()
            self._subscribers_by_run.setdefault(run_id, []).append(q)
            return q

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subscribers = self._subscribers_by_run.get(run_id, [])
            self._subscribers_by_run[run_id] = [q for q in subscribers if q is not queue]


def normalize_usage(raw: Any) -> Optional[Dict[str, int]]:
    if not isinstance(raw, dict):
        return None

    prompt_tokens = raw.get("prompt_tokens")
    completion_tokens = raw.get("completion_tokens")
    total_tokens = raw.get("total_tokens")

    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None

    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    total_tokens = int(total_tokens or (prompt_tokens + completion_tokens))

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def extract_usage_from_llm_response(response: Any) -> Optional[Dict[str, int]]:
    usage = None

    usage_meta = getattr(response, "usage_metadata", None)
    usage = normalize_usage(usage_meta)
    if usage:
        return usage

    response_meta = getattr(response, "response_metadata", None)
    if isinstance(response_meta, dict):
        usage = normalize_usage(response_meta.get("token_usage"))
        if usage:
            return usage

    additional_kwargs = getattr(response, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        usage = normalize_usage(additional_kwargs.get("usage"))
        if usage:
            return usage

    return None


trace_runtime = TraceRuntime()
