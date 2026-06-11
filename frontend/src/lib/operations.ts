/** Long-running operation polling (SSE first, long-poll fallback). */

import { apiFetch } from "./api";

export interface Operation {
  id: string;
  status: string;
  progress: number;
  progress_message?: string;
  error?: string;
  result?: Record<string, unknown>;
}

const ACTIVE_STATUSES = new Set(["pending", "running", "cancelling"]);

export interface PollOptions {
  timeoutMs?: number;
  onProgress?: (pct: number, message?: string) => void;
}

export async function pollOperation(
  operationId: string,
  { timeoutMs = 300000, onProgress }: PollOptions = {},
): Promise<Operation> {
  if (typeof EventSource !== "undefined") {
    try {
      return await pollSSE(operationId, timeoutMs, onProgress);
    } catch {
      // SSE failed — fall through to long-poll.
    }
  }
  return pollLongPoll(operationId, timeoutMs, onProgress);
}

function pollSSE(
  operationId: string,
  timeoutMs: number,
  onProgress?: PollOptions["onProgress"],
): Promise<Operation> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      source.close();
      reject(new Error("Operation timed out waiting for completion"));
    }, timeoutMs);
    const source = new EventSource(`/api/operations/${operationId}/stream`);
    source.onmessage = (event) => {
      const op: Operation = JSON.parse(event.data);
      if (onProgress && op.progress > 0) onProgress(op.progress, op.progress_message);
      if (!ACTIVE_STATUSES.has(op.status)) {
        source.close();
        clearTimeout(timer);
        resolve(op);
      }
    };
    source.onerror = () => {
      source.close();
      clearTimeout(timer);
      reject(new Error("SSE connection failed"));
    };
  });
}

async function pollLongPoll(
  operationId: string,
  timeoutMs: number,
  onProgress?: PollOptions["onProgress"],
): Promise<Operation> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const remainingSecs = Math.min(30, Math.ceil((deadline - Date.now()) / 1000));
    const op = await apiFetch<Operation>(`/api/operations/${operationId}?wait=${remainingSecs}`);
    if (onProgress && op.progress > 0) onProgress(op.progress, op.progress_message);
    if (!ACTIVE_STATUSES.has(op.status)) return op;
  }
  throw new Error("Operation timed out waiting for completion");
}
