import { apiStream } from "./api";
import type { AssistantCitation } from "./types";

export const ASSISTANT_REQUEST_FAILED =
  "The study assistant is temporarily unavailable. Please try again.";

export interface AssistantStreamHandlers {
  onToken: (text: string) => void;
  onCitations: (citations: AssistantCitation[], refusal: boolean) => void;
  onError: (message: string) => void;
  onDone: (latencyMs: number | null) => void;
}

export interface AskAssistantInput {
  question: string;
  courseId?: string;
  topicId?: string;
  signal?: AbortSignal;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function asCitation(value: unknown): AssistantCitation | null {
  const record = asRecord(value);
  if (
    typeof record.document_id !== "string" ||
    typeof record.title !== "string" ||
    typeof record.page !== "number"
  ) {
    return null;
  }
  return {
    document_id: record.document_id,
    title: record.title,
    page: record.page,
  };
}

/** Ask the release-gated study assistant and dispatch streamed SSE events. */
export async function askAssistant(
  input: AskAssistantInput,
  handlers: AssistantStreamHandlers,
): Promise<void> {
  await apiStream(
    "/api/v1/student/ask",
    {
      method: "POST",
      body: JSON.stringify({
        question: input.question,
        course_id: input.courseId ?? null,
        topic_id: input.topicId ?? null,
      }),
      signal: input.signal,
    },
    (event, data) => {
      const record = asRecord(data);

      if (event === "token") {
        if (typeof record.text === "string") handlers.onToken(record.text);
        return;
      }

      if (event === "citations") {
        const citations = Array.isArray(record.citations)
          ? record.citations
              .map(asCitation)
              .filter((citation): citation is AssistantCitation => citation !== null)
          : [];
        handlers.onCitations(citations, record.refusal === true);
        return;
      }

      if (event === "error") {
        const message = record.message;
        handlers.onError(
          typeof message === "string" && message ? message : ASSISTANT_REQUEST_FAILED,
        );
        return;
      }

      if (event === "done") {
        const latency = record.latency_ms;
        handlers.onDone(typeof latency === "number" ? latency : null);
      }
    },
  );
}
