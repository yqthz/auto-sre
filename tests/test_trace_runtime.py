from app.agent.trace_runtime import TraceRuntime


def test_list_runs_filters_by_user_and_session():
    runtime = TraceRuntime()

    user_run = runtime.start_run(session_id=1, user_id=10, mode="manual")
    other_user_run = runtime.start_run(session_id=1, user_id=20, mode="manual")
    other_session_run = runtime.start_run(session_id=2, user_id=10, mode="auto")

    runtime.end_run(user_run, status="success")
    runtime.end_run(other_user_run, status="failed")
    runtime.end_run(other_session_run, status="success")

    payload = runtime.list_runs(user_id=10, session_id=1)

    assert payload["total"] == 1
    assert payload["runs"][0]["run_id"] == user_run


def test_session_summary_aggregates_owned_runs():
    runtime = TraceRuntime()

    first_run = runtime.start_run(session_id=1, user_id=10, mode="manual")
    second_run = runtime.start_run(session_id=1, user_id=10, mode="manual")
    runtime.start_run(session_id=1, user_id=20, mode="manual")

    runtime.add_usage(first_run, {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8})
    runtime.add_usage(second_run, {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6})
    runtime.end_run(first_run, status="success")

    summary = runtime.get_session_summary(session_id=1, user_id=10)

    assert summary["session_id"] == 1
    assert summary["run_count"] == 2
    assert summary["running_count"] == 1
    assert summary["status_counts"] == {"success": 1, "running": 1}
    assert summary["token_summary"]["prompt_tokens_total"] == 5
    assert summary["token_summary"]["completion_tokens_total"] == 9
    assert summary["token_summary"]["total_tokens"] == 14
    assert summary["token_summary"]["llm_calls_count"] == 2
    assert summary["latest_run_id"] == second_run
    assert summary["latest_start_ts"] is not None


def test_session_events_are_sorted_and_filtered_by_owner():
    runtime = TraceRuntime()

    first_run = runtime.start_run(session_id=1, user_id=10, mode="manual")
    second_run = runtime.start_run(session_id=1, user_id=10, mode="manual")
    other_user_run = runtime.start_run(session_id=1, user_id=20, mode="manual")

    first_event = runtime.append_event(
        run_id=first_run,
        event_type="llm_call_start",
        call_id="llm-1",
        status="running",
        meta={},
    )
    second_event = runtime.append_event(
        run_id=second_run,
        event_type="tool_call_start",
        call_id="tool-1",
        status="running",
        meta={},
    )
    runtime.append_event(
        run_id=other_user_run,
        event_type="llm_call_start",
        call_id="llm-other",
        status="running",
        meta={},
    )

    payload = runtime.get_session_events(session_id=1, user_id=10)

    assert payload["exists"] is True
    assert {event["call_id"] for event in payload["events"]} == {"llm-1", "tool-1"}
    assert payload["latest_seq_by_run"] == {first_run: 1, second_run: 1}
    assert payload["latest_ts"] == max(first_event["ts"], second_event["ts"])


def test_list_session_summaries_returns_latest_sessions_first_and_filters():
    runtime = TraceRuntime()

    first_session_run = runtime.start_run(session_id=1, user_id=10, mode="manual")
    second_session_run = runtime.start_run(session_id=2, user_id=10, mode="manual")
    runtime.start_run(session_id=3, user_id=20, mode="manual")

    runtime.end_run(first_session_run, status="failed")
    runtime.end_run(second_session_run, status="success")

    all_sessions = runtime.list_session_summaries(user_id=10)
    failed_sessions = runtime.list_session_summaries(user_id=10, status="failed")

    assert [item["session_id"] for item in all_sessions] == [2, 1]
    assert [item["session_id"] for item in failed_sessions] == [1]
    assert failed_sessions[0]["status_counts"] == {"failed": 1}


def test_visible_user_ids_allow_system_trace_visibility():
    runtime = TraceRuntime()

    user_run = runtime.start_run(session_id=1, user_id=10, mode="manual")
    bot_run = runtime.start_run(
        session_id=2,
        user_id=99,
        mode="auto",
        trigger_type="alert",
        alert_event_id=123,
        thread_id="thread_abc",
    )

    user_only = runtime.list_runs(user_id=10)
    with_bot = runtime.list_runs(user_id=10, visible_user_ids=[99])

    assert [item["run_id"] for item in user_only["runs"]] == [user_run]
    assert [item["run_id"] for item in with_bot["runs"]] == [bot_run, user_run]
    assert with_bot["runs"][0]["trigger_type"] == "alert"
    assert with_bot["runs"][0]["alert_event_id"] == 123
    assert with_bot["runs"][0]["thread_id"] == "thread_abc"
    assert runtime.check_owner(bot_run, user_id=10, visible_user_ids=[99]) is True
    assert runtime.check_owner(bot_run, user_id=10) is False
