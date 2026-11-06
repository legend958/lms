"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { Textarea } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { ASSISTANT_REQUEST_FAILED, askAssistant } from "@/lib/assistant";
import { useMe, useStudentTopics } from "@/lib/hooks";
import type { AssistantCitation, StudentTopicView } from "@/lib/types";
import { cn } from "@/lib/utils";

const SUGGESTION_LIMIT = 4;
const TOPIC_LIST_LIMIT = 8;
const MAX_QUESTION = 1000;

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: AssistantCitation[];
  refusal: boolean;
  error: string | null;
  pending: boolean;
}

let messageSequence = 0;

function nextMessageId(prefix: string): string {
  messageSequence += 1;
  return `${prefix}-${Date.now()}-${messageSequence}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return "You have reached the study assistant limit for this hour. Try again later.";
    }
    return error.message;
  }
  return ASSISTANT_REQUEST_FAILED;
}

function topicSuggestion(topic: StudentTopicView): string {
  return `Explain the key ideas in "${topic.title}".`;
}

function citationNumbers(text: string): number[] {
  const seen = new Set<number>();
  const numbers: number[] = [];
  for (const match of text.matchAll(/\[(\d+)\]/g)) {
    const value = Number(match[1]);
    if (!Number.isInteger(value) || seen.has(value)) continue;
    seen.add(value);
    numbers.push(value);
  }
  return numbers;
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 py-1" aria-label="Assistant is writing">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-400"
          style={{ animationDelay: `${index * 120}ms` }}
        />
      ))}
    </span>
  );
}

function CitationList({ text, citations }: { text: string; citations: AssistantCitation[] }) {
  const numbers = citationNumbers(text);

  return (
    <div className="mt-3 border-t border-ink-100 pt-2">
      <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-500">
        Sources
      </p>
      <div className="flex flex-wrap gap-1.5">
        {citations.map((citation, index) => (
          <span
            key={`${citation.document_id}-${citation.page}-${index}`}
            title={`${citation.title} · page ${citation.page}`}
            className="inline-flex max-w-full items-center gap-1 rounded-md bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-800 ring-1 ring-inset ring-indigo-100"
          >
            <span className="font-semibold">[{numbers[index] ?? index + 1}]</span>
            <span className="truncate">{citation.title}</span>
            <span className="shrink-0 text-indigo-500">p. {citation.page}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export function StudentAssistant() {
  const { data: me } = useMe();
  const [courseId, setCourseId] = useState("");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const topicsQuery = useStudentTopics(courseId || undefined);
  const topics = topicsQuery.data ?? [];
  const courses = me?.courses ?? [];
  const suggestions = useMemo(() => topics.slice(0, SUGGESTION_LIMIT), [topics]);

  const abortRef = useRef<AbortController | null>(null);
  const activeRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = scrollRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function submit(rawQuestion: string, topicId?: string) {
    const question = rawQuestion.trim();
    if (!question || pending) return;

    setError(null);
    setInput("");
    setPending(true);

    const userMessage: ChatMessage = {
      id: nextMessageId("user"),
      role: "user",
      text: question,
      citations: [],
      refusal: false,
      error: null,
      pending: false,
    };
    const assistantId = nextMessageId("assistant");
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      citations: [],
      refusal: false,
      error: null,
      pending: true,
    };
    setMessages((current) => [...current, userMessage, assistantMessage]);

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = nextMessageId("request");
    activeRef.current = requestId;

    const update = (change: (message: ChatMessage) => ChatMessage) => {
      if (activeRef.current !== requestId) return;
      setMessages((current) =>
        current.map((message) => (message.id === assistantId ? change(message) : message)),
      );
    };

    try {
      await askAssistant(
        {
          question,
          courseId: courseId || undefined,
          topicId,
          signal: controller.signal,
        },
        {
          onToken: (text) =>
            update((message) => ({ ...message, text: message.text + text })),
          onCitations: (citations, refusal) =>
            update((message) => ({ ...message, citations, refusal })),
          onError: (message) =>
            update((current) => ({ ...current, error: message })),
          onDone: () =>
            update((message) => ({ ...message, pending: false })),
        },
      );
    } catch (caught) {
      if (activeRef.current !== requestId) return;
      if (controller.signal.aborted) {
        update((message) => ({ ...message, pending: false }));
        return;
      }
      const message = errorMessage(caught);
      setError(message);
      update((current) => ({ ...current, error: message, pending: false }));
    } finally {
      if (activeRef.current === requestId) {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId ? { ...message, pending: false } : message,
          ),
        );
        setPending(false);
        activeRef.current = null;
        abortRef.current = null;
      }
    }
  }

  function stop() {
    const controller = abortRef.current;
    activeRef.current = null;
    abortRef.current = null;
    controller?.abort();
    setMessages((current) =>
      current.map((message) => (message.pending ? { ...message, pending: false } : message)),
    );
    setPending(false);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit(input);
    }
  }

  return (
    <div>
      <PageHeader
        title="Study assistant"
        description="Ask questions about the material your teachers have released. Answers cite the document and page."
        actions={<Badge tone="indigo">Released material only</Badge>}
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Card className="flex min-h-[560px] flex-col overflow-hidden">
          <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Chat</CardTitle>
              <p className="mt-1 text-xs text-ink-500">
                Scope applies to each new question. Draft topics are never searched.
              </p>
            </div>
            <label className="flex items-center gap-2 text-xs font-medium text-ink-500">
              Course
              <select
                value={courseId}
                onChange={(event) => {
                  setCourseId(event.target.value);
                  setError(null);
                }}
                disabled={pending}
                className="h-9 max-w-[220px] rounded-lg border border-ink-200 bg-white px-2 text-xs text-ink-800 shadow-sm focus:border-indigo-400 focus:outline-none disabled:bg-ink-50"
              >
                <option value="">All my courses</option>
                {courses.map((course) => (
                  <option key={course.id} value={course.id}>
                    {course.code} — {course.title}
                  </option>
                ))}
              </select>
            </label>
          </CardHeader>

          <div
            ref={scrollRef}
            className="flex-1 space-y-4 overflow-y-auto bg-ink-50/40 px-4 py-5 sm:px-5"
            style={{ maxHeight: "56vh" }}
          >
            {messages.length === 0 ? (
              <div className="flex h-full min-h-[300px] flex-col items-center justify-center px-4 text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white shadow-lg shadow-indigo-900/20">
                  ?
                </div>
                <h2 className="mt-4 text-base font-semibold text-ink-900">
                  Ask about your released material
                </h2>
                <p className="mt-1 max-w-md text-sm text-ink-500">
                  The assistant reads only the topics your teacher has released. Every answer
                  ends with the pages it used.
                </p>

                {suggestions.length > 0 ? (
                  <div className="mt-5 flex max-w-lg flex-wrap justify-center gap-2">
                    {suggestions.map((topic) => (
                      <button
                        key={topic.id}
                        type="button"
                        disabled={pending}
                        onClick={() => void submit(topicSuggestion(topic), topic.id)}
                        className="rounded-full border border-ink-200 bg-white px-3 py-1.5 text-xs font-medium text-ink-700 shadow-sm transition-colors hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {topicSuggestion(topic)}
                      </button>
                    ))}
                  </div>
                ) : topicsQuery.isLoading ? (
                  <div className="mt-5">
                    <Spinner label="Loading released topics" />
                  </div>
                ) : (
                  <p className="mt-5 max-w-sm text-xs text-ink-500">
                    No released topics yet. Once your teacher releases a topic, it will appear
                    here as a suggestion.
                  </p>
                )}
              </div>
            ) : (
              messages.map((message) => (
                <div
                  key={message.id}
                  className={cn(
                    "flex",
                    message.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  <div
                    className={cn(
                      "max-w-[92%] rounded-2xl px-4 py-3 text-sm shadow-sm sm:max-w-[85%]",
                      message.role === "user"
                        ? "bg-indigo-600 text-white"
                        : "border border-ink-200 bg-white text-ink-800",
                    )}
                  >
                    {message.role === "assistant" ? (
                      <>
                        <div className="mb-1 flex items-center gap-2">
                          <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">
                            Study assistant
                          </span>
                          {message.refusal ? <Badge tone="warning">Not covered</Badge> : null}
                        </div>

                        {message.error ? (
                          <p className="mb-2 rounded-md bg-rose-50 px-2 py-1 text-xs text-rose-700">
                            {message.error}
                          </p>
                        ) : null}

                        {message.text ? (
                          <p className="whitespace-pre-wrap leading-relaxed">{message.text}</p>
                        ) : message.pending ? (
                          <TypingDots />
                        ) : null}

                        {message.citations.length > 0 ? (
                          <CitationList text={message.text} citations={message.citations} />
                        ) : null}
                      </>
                    ) : (
                      <p className="whitespace-pre-wrap leading-relaxed">{message.text}</p>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

          {error ? (
            <div className="border-t border-ink-100 px-4 py-3 sm:px-5">
              <ErrorBanner message={error} />
            </div>
          ) : null}

          <form onSubmit={handleSubmit} className="border-t border-ink-100 bg-white px-4 py-4 sm:px-5">
            <div className="flex items-end gap-2">
              <Textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about a released chapter…"
                rows={2}
                maxLength={MAX_QUESTION}
                aria-label="Your question"
                disabled={pending}
                className="resize-none"
              />
              {pending ? (
                <Button variant="secondary" onClick={stop} className="shrink-0">
                  Stop
                </Button>
              ) : (
                <Button type="submit" disabled={!input.trim()} className="shrink-0">
                  Send
                </Button>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink-500">
              <span>Enter to send · Shift+Enter for a new line</span>
              <span>
                {input.length}/{MAX_QUESTION}
              </span>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-ink-500">
              Answers are based on released course material. If the material does not cover your
              question, the assistant says so instead of guessing.
            </p>
          </form>
        </Card>

        <aside className="space-y-4">
          <Card>
            <CardHeader className="flex items-center justify-between gap-2">
              <CardTitle>Released topics</CardTitle>
              <Badge tone="info">{topics.length}</Badge>
            </CardHeader>
            <CardContent className="space-y-2">
              {topicsQuery.isLoading ? <Spinner label="Loading topics" /> : null}

              {topicsQuery.error ? (
                <p className="text-sm text-rose-700">Could not load released topics.</p>
              ) : null}

              {!topicsQuery.isLoading && !topicsQuery.error && topics.length === 0 ? (
                <p className="text-sm text-ink-500">
                  Nothing released yet. Your teacher controls what the assistant can see.
                </p>
              ) : null}

              {topics.slice(0, TOPIC_LIST_LIMIT).map((topic) => {
                const course = courses.find((item) => item.id === topic.course_id);
                return (
                  <button
                    key={topic.id}
                    type="button"
                    disabled={pending}
                    onClick={() => void submit(topicSuggestion(topic), topic.id)}
                    className="block w-full rounded-lg border border-ink-100 px-3 py-2 text-left transition-colors hover:border-indigo-200 hover:bg-indigo-50/50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <p className="text-xs font-semibold text-ink-800">{topic.title}</p>
                    <p className="mt-0.5 text-[11px] text-ink-500">
                      {course ? `${course.code} · ` : ""}pages {topic.page_start}–{topic.page_end}
                    </p>
                  </button>
                );
              })}

              {topics.length > TOPIC_LIST_LIMIT ? (
                <p className="text-[11px] text-ink-500">
                  +{topics.length - TOPIC_LIST_LIMIT} more released topics
                </p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>How answers are built</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs leading-relaxed text-ink-600">
              <p>1. Your question is matched against released chunks only.</p>
              <p>2. The model answers from those chunks and cites each claim.</p>
              <p>3. No matching material means a refusal, not a guess.</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Privacy</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs leading-relaxed text-ink-600">
                Questions are logged without your name and without raw question text by default.
                Teachers see topic-level trends, not your chat history.
              </p>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
