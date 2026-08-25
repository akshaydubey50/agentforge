"use client";

// Subscribes to one task's live event stream (GET /v1/tasks/{id}/events).
//
// This does NOT replace the SWR fetches on a task page, and that is on
// purpose. The stream carries "what is happening now"; Postgres, via those
// fetches, remains the record of what happened. So the pattern here is:
// events arrive -> revalidate the real data. The UI gets stream latency with
// fetch correctness, and a dropped connection degrades to exactly the
// polling behaviour that existed before, rather than to a blank screen.

import { useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "./api";

export interface TaskEvent {
  kind: string;
  task_id: string;
  ts: string;
  span_id?: string;
  span_type?: string;
  name?: string;
  subtask_id?: string | null;
  status?: string;
  previous?: string;
  duration_ms?: number;
  input?: unknown;
  output?: unknown;
  detail?: string;
}

export type StreamState = "connecting" | "open" | "closed" | "error";

const EVENT_KINDS = ["snapshot", "span_start", "span_end", "task_status", "stream_error"];

export function useTaskEvents(
  taskId: string | null,
  options: {
    /** Called for every event, in arrival order. Use it to revalidate. */
    onEvent?: (event: TaskEvent) => void;
    /** Keep at most this many events in the returned buffer. */
    limit?: number;
    enabled?: boolean;
  } = {}
) {
  const { onEvent, limit = 200, enabled = true } = options;
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [state, setState] = useState<StreamState>("closed");

  // The callback is read through a ref so a caller passing an inline arrow
  // (the normal case) doesn't tear down and re-open the EventSource on every
  // render -- which would reconnect several times a second and lose events.
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!taskId || !enabled) {
      setState("closed");
      return;
    }

    setEvents([]);
    setState("connecting");

    // credentials are required: the endpoint is behind the same session
    // cookie as every other call, and EventSource omits them by default.
    const source = new EventSource(`${API_BASE_URL}/v1/tasks/${taskId}/events`, {
      withCredentials: true,
    });

    const handle = (event: MessageEvent) => {
      let parsed: TaskEvent;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        return;
      }
      setState("open");
      setEvents((current) => {
        const next = [...current, parsed];
        return next.length > limit ? next.slice(next.length - limit) : next;
      });
      onEventRef.current?.(parsed);
    };

    for (const kind of EVENT_KINDS) source.addEventListener(kind, handle as EventListener);

    source.onopen = () => setState("open");
    source.onerror = () => {
      // EventSource reconnects on its own; this only reports the gap. The
      // page's SWR polling is still running underneath, so an outage here
      // costs freshness, never correctness.
      setState((current) => (current === "open" ? "connecting" : "error"));
    };

    return () => {
      for (const kind of EVENT_KINDS) source.removeEventListener(kind, handle as EventListener);
      source.close();
      setState("closed");
    };
  }, [taskId, enabled, limit]);

  return { events, state };
}
