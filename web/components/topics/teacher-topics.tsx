"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorBanner, Spinner } from "@/components/ui/feedback";
import { Field, Input, Textarea } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  DocumentRecord,
  StudentTopicPreview,
  TopicRecord,
  TopicReleaseResult,
} from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

function defaultDueAt(): string {
  const due = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
  due.setMinutes(due.getMinutes() - due.getTimezoneOffset());
  return due.toISOString().slice(0, 16);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function StateBadge({ topic }: { topic: TopicRecord }) {
  if (topic.release_state === "released") {
    return <Badge tone="success">Released</Badge>;
  }
  if (topic.release_state === "scheduled") {
    return <Badge tone="info">Scheduled</Badge>;
  }
  return <Badge tone="warning">Draft</Badge>;
}

export function TeacherTopics({ documentId }: { documentId: string }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editSummary, setEditSummary] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeTitle, setMergeTitle] = useState("");
  const [releaseTargets, setReleaseTargets] = useState<TopicRecord[]>([]);
  const [weekNo, setWeekNo] = useState("1");
  const [dueAt, setDueAt] = useState(defaultDueAt());
  const [generateQuiz, setGenerateQuiz] = useState(true);
  const [splitTarget, setSplitTarget] = useState<TopicRecord | null>(null);
  const [splitChunkId, setSplitChunkId] = useState("");
  const [splitTitle, setSplitTitle] = useState("");
  const [previewTarget, setPreviewTarget] = useState<TopicRecord | null>(null);

  const documentQuery = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => apiFetch<DocumentRecord>(`/api/v1/documents/${documentId}`),
  });

  const topicsQuery = useQuery({
    queryKey: ["topics", documentId],
    queryFn: () => apiFetch<TopicRecord[]>(`/api/v1/documents/${documentId}/topics`),
  });

  const topics = useMemo(() => topicsQuery.data ?? [], [topicsQuery.data]);
  const draftTopics = topics.filter((topic) => topic.release_state === "draft");
  const releasedTopics = topics.filter((topic) => topic.release_state === "released");
  const selectedTopics = topics.filter((topic) => selected.has(topic.id));

  const previewQuery = useQuery({
    queryKey: ["topic-preview", previewTarget?.id ?? "none"],
    queryFn: () => apiFetch<StudentTopicPreview>(`/api/v1/topics/${previewTarget?.id}/preview`),
    enabled: Boolean(previewTarget),
  });

  const splitPreviewQuery = useQuery({
    queryKey: ["topic-preview", splitTarget?.id ?? "none"],
    queryFn: () => apiFetch<StudentTopicPreview>(`/api/v1/topics/${splitTarget?.id}/preview`),
    enabled: Boolean(splitTarget),
  });

  useEffect(() => {
    if (releaseTargets.length > 0) setGenerateQuiz(true);
  }, [releaseTargets]);

  useEffect(() => {
    if (!splitTarget) return;
    const chunks = splitPreviewQuery.data?.chunks;
    if (chunks && chunks.length > 1) {
      setSplitChunkId(chunks[1].id);
      setSplitTitle(`${splitTarget.title} (continued)`);
    }
  }, [splitPreviewQuery.data, splitTarget]);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["topics", documentId] });
    void queryClient.invalidateQueries({ queryKey: ["document", documentId] });
    void queryClient.invalidateQueries({ queryKey: ["documents"] });
    void queryClient.invalidateQueries({ queryKey: ["topic-preview"] });
  }

  const update = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Record<string, unknown> }) =>
      apiFetch<TopicRecord>(`/api/v1/topics/${id}`, {
        method: "PATCH",
        body: JSON.stringify(changes),
      }),
    onSuccess: () => {
      setEditingId(null);
      setActionError(null);
      invalidate();
    },
    onError: (error) => setActionError(errorMessage(error, "Could not update the topic.")),
  });

  const release = useMutation({
    mutationFn: ({
      topicId,
      week,
      due,
      withQuiz,
    }: {
      topicId: string;
      week: number;
      due: string;
      withQuiz: boolean;
    }) =>
      apiFetch<TopicReleaseResult>(`/api/v1/topics/${topicId}/release`, {
        method: "POST",
        body: JSON.stringify({ week_no: week, due_at: due, generate_quiz: withQuiz }),
      }),
    onSuccess: (result) => {
      if (!result.chroma_synced) {
        setNotice("Released. The search index will finish syncing in the background.");
      } else if (result.quiz_id) {
        setNotice(
          result.quiz_queued
            ? "Released. A quiz is generating for teacher review."
            : "Released. A draft quiz was created; generation will retry from the Quizzes page.",
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["teacher-quizzes"] });
      invalidate();
    },
    onError: (error) => setActionError(errorMessage(error, "Could not release the topic.")),
  });

  const unrelease = useMutation({
    mutationFn: (topicId: string) =>
      apiFetch<{ release_state: string }>(`/api/v1/topics/${topicId}/unrelease`, {
        method: "POST",
      }),
    onSuccess: () => {
      setActionError(null);
      setNotice("Topic hidden from students.");
      invalidate();
    },
    onError: (error) => setActionError(errorMessage(error, "Could not unrelease the topic.")),
  });

  const remove = useMutation({
    mutationFn: (topicId: string) =>
      apiFetch<void>(`/api/v1/topics/${topicId}`, { method: "DELETE" }),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (error) => setActionError(errorMessage(error, "Could not delete the topic.")),
  });

  const merge = useMutation({
    mutationFn: ({ ids, title }: { ids: string[]; title: string }) =>
      apiFetch<TopicRecord>("/api/v1/topics/merge", {
        method: "POST",
        body: JSON.stringify({ topic_ids: ids, title }),
      }),
    onSuccess: () => {
      setMergeOpen(false);
      setSelected(new Set());
      setNotice("Topics merged.");
      invalidate();
    },
    onError: (error) => setActionError(errorMessage(error, "Could not merge the topics.")),
  });

  const split = useMutation({
    mutationFn: ({
      id,
      chunkId,
      title,
    }: {
      id: string;
      chunkId: string;
      title: string;
    }) =>
      apiFetch<TopicRecord>(`/api/v1/topics/${id}/split`, {
        method: "POST",
        body: JSON.stringify({ at_chunk_id: chunkId, new_title: title }),
      }),
    onSuccess: () => {
      setSplitTarget(null);
      setNotice("Topic split.");
      invalidate();
    },
    onError: (error) => setActionError(errorMessage(error, "Could not split the topic.")),
  });

  async function submitRelease() {
    if (releaseTargets.length === 0) return;
    const week = Math.max(1, Math.min(52, Number(weekNo) || 1));
    try {
      const results: TopicReleaseResult[] = [];
      for (const topic of releaseTargets) {
        results.push(
          await release.mutateAsync({
            topicId: topic.id,
            week,
            due: new Date(dueAt).toISOString(),
            withQuiz: generateQuiz,
          }),
        );
      }
      const quizCount = results.filter((result) => result.quiz_id).length;
      setReleaseTargets([]);
      const releasedLabel =
        releaseTargets.length === 1
          ? "Topic released to students."
          : `${releaseTargets.length} topics released to students.`;
      setNotice(
        quizCount > 0
          ? `${releasedLabel} ${quizCount === 1 ? "A quiz is" : `${quizCount} quizzes are`} generating for review.`
          : releasedLabel,
      );
      setSelected(new Set());
    } catch {
      // The mutation callback sets a specific message.
    }
  }

  function toggleSelected(topicId: string) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(topicId)) next.delete(topicId);
      else next.add(topicId);
      return next;
    });
  }

  function startEdit(topic: TopicRecord) {
    setEditingId(topic.id);
    setEditTitle(topic.title);
    setEditSummary(topic.summary);
  }

  function saveEdit(topic: TopicRecord) {
    update.mutate({
      id: topic.id,
      changes: {
        title: editTitle.trim(),
        summary: editSummary.trim(),
      },
    });
  }

  function move(topic: TopicRecord, direction: -1 | 1) {
    const index = topics.findIndex((item) => item.id === topic.id);
    const neighbor = topics[index + direction];
    if (!neighbor) return;
    update.mutate({ id: topic.id, changes: { order_index: neighbor.order_index } });
  }

  if (documentQuery.isLoading || topicsQuery.isLoading) {
    return <Spinner label="Loading topics" />;
  }

  if (documentQuery.error || topicsQuery.error) {
    return <ErrorBanner message="Could not load this document's topics." />;
  }

  if (!documentQuery.data) {
    return <ErrorBanner message="Document not found." />;
  }

  const document = documentQuery.data;
  const notReady = document.status !== "ready";
  const fallback = document.topics_source === "fallback";
  const totalChunks = topics.reduce((sum, topic) => sum + topic.chunk_count, 0);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Link
            href="/teacher/documents"
            className="text-xs font-semibold uppercase tracking-wide text-indigo-600 hover:text-indigo-700"
          >
            Back to documents
          </Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink-950">
            Review topics
          </h1>
          <p className="mt-1 text-sm text-ink-500">
            {document.filename} - {document.page_count ?? "?"} pages -{" "}
            {document.course_code}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => setReleaseTargets([...draftTopics])}
            disabled={draftTopics.length === 0 || release.isPending || notReady}
          >
            Release all drafts
          </Button>
        </div>
      </div>

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}
      {notice ? (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {notice}
        </div>
      ) : null}

      {notReady ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Ingestion is still finishing ({document.status.replaceAll("_", " ")}). Topics appear
          when the document reaches <strong>ready</strong>.
        </div>
      ) : null}

      {fallback ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Topic extraction used the heading fallback, so every topic is marked for review.
          Check titles, summaries, and page ranges before releasing.
        </div>
      ) : null}

      <div className="mb-6 grid gap-4 sm:grid-cols-4">
        {[
          { label: "Topics", value: topics.length, hint: "In this document" },
          { label: "Draft", value: draftTopics.length, hint: "Hidden from students" },
          { label: "Released", value: releasedTopics.length, hint: "Visible to students" },
          { label: "Chunks", value: totalChunks, hint: "Assigned to topics" },
        ].map((stat) => (
          <Card key={stat.label}>
            <CardContent>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                {stat.label}
              </p>
              <p className="mt-2 text-3xl font-semibold text-ink-950">{stat.value}</p>
              <p className="mt-1 text-xs text-ink-500">{stat.hint}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {selectedTopics.length > 0 ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3">
          <span className="text-sm font-medium text-indigo-800">
            {selectedTopics.length} draft{selectedTopics.length === 1 ? "" : "s"} selected
          </span>
          <Button
            size="sm"
            variant="secondary"
            disabled={selectedTopics.length < 2}
            onClick={() => {
              setMergeTitle(selectedTopics[0]?.title ?? "Merged topic");
              setMergeOpen(true);
            }}
          >
            Merge
          </Button>
          <Button
            size="sm"
            disabled={release.isPending || notReady}
            onClick={() => setReleaseTargets(selectedTopics)}
          >
            Release selected
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      ) : null}

      <Card>
        <CardHeader className="flex items-center justify-between gap-3">
          <CardTitle>Topic list</CardTitle>
          <span className="text-xs text-ink-500">
            {draftTopics.length} draft - {releasedTopics.length} released
          </span>
        </CardHeader>

        {topics.length === 0 ? (
          <CardContent>
            <EmptyState
              title="No topics yet"
              description="Topics are created automatically after ingestion. If this document is ready and has no topics, re-ingest it."
            />
          </CardContent>
        ) : (
          <div className="divide-y divide-ink-100">
            {topics.map((topic, index) => {
              const isDraft = topic.release_state === "draft";
              const isEditing = editingId === topic.id;
              return (
                <div key={topic.id} className="px-5 py-5">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div className="flex min-w-0 gap-3">
                      {isDraft ? (
                        <input
                          type="checkbox"
                          aria-label={`Select ${topic.title}`}
                          checked={selected.has(topic.id)}
                          onChange={() => toggleSelected(topic.id)}
                          className="mt-1 h-4 w-4 rounded border-ink-300 text-indigo-600"
                        />
                      ) : (
                        <span className="mt-1 h-4 w-4" />
                      )}
                      <div className="min-w-0">
                        {isEditing ? (
                          <div className="space-y-3">
                            <Field label="Title">
                              <Input
                                value={editTitle}
                                maxLength={90}
                                onChange={(event) => setEditTitle(event.target.value)}
                              />
                            </Field>
                            <Field label="Summary">
                              <Textarea
                                value={editSummary}
                                maxLength={2000}
                                onChange={(event) => setEditSummary(event.target.value)}
                              />
                            </Field>
                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                disabled={update.isPending || editTitle.trim().length < 3}
                                onClick={() => saveEdit(topic)}
                              >
                                Save
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setEditingId(null)}
                              >
                                Cancel
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-xs font-semibold text-ink-500">
                                {String(index + 1).padStart(2, "0")}
                              </span>
                              <p className="text-sm font-semibold text-ink-900">{topic.title}</p>
                              <StateBadge topic={topic} />
                              {topic.needs_review ? (
                                <Badge tone="warning">Needs review</Badge>
                              ) : null}
                              {topic.task_status === "published" ? (
                                <Badge tone="indigo">Task published</Badge>
                              ) : null}
                            </div>
                            <p className="mt-2 max-w-2xl text-sm text-ink-600">
                              {topic.summary || "No summary yet."}
                            </p>
                            <p className="mt-2 text-xs text-ink-500">
                              Pages {topic.page_start}-{topic.page_end} - {topic.chunk_count}{" "}
                              chunk{topic.chunk_count === 1 ? "" : "s"}
                              {topic.released_at
                                ? ` - released ${formatDateTime(topic.released_at)}`
                                : ""}
                            </p>
                          </>
                        )}
                      </div>
                    </div>

                    <div className="flex shrink-0 flex-wrap items-center gap-1 lg:justify-end">
                      {isDraft ? (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={index === 0 || update.isPending}
                            onClick={() => move(topic, -1)}
                          >
                            Up
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={index === topics.length - 1 || update.isPending}
                            onClick={() => move(topic, 1)}
                          >
                            Down
                          </Button>
                        </>
                      ) : null}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setPreviewTarget(topic)}
                      >
                        Preview
                      </Button>
                      {isDraft ? (
                        <>
                          <Button size="sm" variant="secondary" onClick={() => startEdit(topic)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={isEditing || topic.chunk_count < 2}
                            onClick={() => {
                              setSplitTarget(topic);
                              setSplitChunkId("");
                            }}
                          >
                            Split
                          </Button>
                          <Button
                            size="sm"
                            disabled={release.isPending || notReady}
                            onClick={() => setReleaseTargets([topic])}
                          >
                            Release
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                            disabled={remove.isPending}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete "${topic.title}"? Its chunks move to a neighbouring topic.`,
                                )
                              ) {
                                remove.mutate(topic.id);
                              }
                            }}
                          >
                            Delete
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={unrelease.isPending}
                          onClick={() => {
                            if (
                              window.confirm(
                                `Hide "${topic.title}" from students? The published task is archived.`,
                              )
                            ) {
                              unrelease.mutate(topic.id);
                            }
                          }}
                        >
                          Unrelease
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Modal
        open={releaseTargets.length > 0}
        onClose={() => setReleaseTargets([])}
        title={
          releaseTargets.length === 1
            ? `Release "${releaseTargets[0]?.title}"`
            : `Release ${releaseTargets.length} topics`
        }
        description="Students will see this content and its task immediately. Nothing is released automatically."
        footer={
          <>
            <Button variant="secondary" onClick={() => setReleaseTargets([])}>
              Cancel
            </Button>
            <Button
              disabled={release.isPending}
              onClick={() => void submitRelease()}
            >
              {release.isPending ? "Releasing..." : "Release to students"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Week number" hint="Tasks are grouped by teaching week.">
            <Input
              type="number"
              min={1}
              max={52}
              value={weekNo}
              onChange={(event) => setWeekNo(event.target.value)}
            />
          </Field>
          <Field label="Due date">
            <Input
              type="datetime-local"
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
            />
          </Field>
          <label className="flex items-start gap-2 rounded-lg border border-ink-200 px-3 py-3 text-sm text-ink-700">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 rounded border-ink-300 text-indigo-600"
              checked={generateQuiz}
              onChange={(event) => setGenerateQuiz(event.target.checked)}
            />
            <span>
              Generate a quiz for this topic
              <span className="mt-0.5 block text-xs text-ink-500">
                A draft MCQ quiz is generated from the topic chunks after release. Students only see
                it after you review and publish it in Quizzes.
              </span>
            </span>
          </label>
          <div className="rounded-lg bg-ink-50 px-3 py-2 text-xs text-ink-600">
            Release checks the topic is a draft, creates one published task, and flips the
            vector metadata. A Chroma failure never blocks the release.
          </div>
        </div>
      </Modal>

      <Modal
        open={mergeOpen}
        onClose={() => setMergeOpen(false)}
        title="Merge draft topics"
        description={`Combine ${selectedTopics.length} topics into one. Chunk totals are preserved.`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setMergeOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={merge.isPending || mergeTitle.trim().length < 3}
              onClick={() =>
                merge.mutate({ ids: selectedTopics.map((topic) => topic.id), title: mergeTitle.trim() })
              }
            >
              {merge.isPending ? "Merging..." : "Merge topics"}
            </Button>
          </>
        }
      >
        <Field label="Combined title">
          <Input
            value={mergeTitle}
            maxLength={90}
            onChange={(event) => setMergeTitle(event.target.value)}
          />
        </Field>
      </Modal>

      <Modal
        open={Boolean(splitTarget)}
        onClose={() => setSplitTarget(null)}
        title={splitTarget ? `Split "${splitTarget.title}"` : "Split topic"}
        description="Choose the first chunk of the new topic. The original keeps the earlier chunks."
        footer={
          <>
            <Button variant="secondary" onClick={() => setSplitTarget(null)}>
              Cancel
            </Button>
            <Button
              disabled={
                split.isPending ||
                !splitChunkId ||
                splitTitle.trim().length < 3
              }
              onClick={() =>
                splitTarget &&
                split.mutate({
                  id: splitTarget.id,
                  chunkId: splitChunkId,
                  title: splitTitle.trim(),
                })
              }
            >
              {split.isPending ? "Splitting..." : "Split topic"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="New topic title">
            <Input
              value={splitTitle}
              maxLength={90}
              onChange={(event) => setSplitTitle(event.target.value)}
            />
          </Field>
          {splitPreviewQuery.isLoading ? (
            <Spinner label="Loading chunks" />
          ) : (
            <Field label="Split before chunk">
              <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-ink-200 p-2">
                {(splitPreviewQuery.data?.chunks ?? []).map((chunk, index) => (
                  <label
                    key={chunk.id}
                    className="flex cursor-pointer gap-2 rounded-lg px-2 py-2 text-sm hover:bg-ink-50"
                  >
                    <input
                      type="radio"
                      name="split-chunk"
                      className="mt-1"
                      checked={splitChunkId === chunk.id}
                      disabled={index === 0}
                      onChange={() => setSplitChunkId(chunk.id)}
                    />
                    <span className="min-w-0">
                      <span className="block text-xs font-semibold text-ink-500">
                        Page {chunk.page_start} - chunk {index + 1}
                      </span>
                      <span className="line-clamp-2 text-ink-700">{chunk.text}</span>
                    </span>
                  </label>
                ))}
              </div>
            </Field>
          )}
        </div>
      </Modal>

      <Modal
        open={Boolean(previewTarget)}
        onClose={() => setPreviewTarget(null)}
        title={previewTarget ? `Student preview: ${previewTarget.title}` : "Student preview"}
        description="This is the shape students receive. Release state and vector metadata are never exposed."
        className="max-w-3xl"
        footer={
          <Button variant="secondary" onClick={() => setPreviewTarget(null)}>
            Close
          </Button>
        }
      >
        {previewQuery.isLoading ? (
          <Spinner label="Loading preview" />
        ) : previewQuery.error ? (
          <ErrorBanner message="Could not load the preview." />
        ) : (
          <div className="space-y-4">
            <div>
              <p className="text-sm font-semibold text-ink-900">
                {previewQuery.data?.topic.title}
              </p>
              <p className="mt-1 text-sm text-ink-600">{previewQuery.data?.topic.summary}</p>
              <p className="mt-2 text-xs text-ink-500">
                Pages {previewQuery.data?.topic.page_start}-{previewQuery.data?.topic.page_end}{" "}
                - {previewQuery.data?.chunks.length} chunk
                {previewQuery.data?.chunks.length === 1 ? "" : "s"}
              </p>
            </div>
            <div className="space-y-3">
              {(previewQuery.data?.chunks ?? []).map((chunk) => (
                <div key={chunk.id} className="rounded-lg border border-ink-200 p-3">
                  <p className="text-xs font-semibold text-ink-500">
                    {chunk.section_title || "Untitled section"} - page {chunk.page_start}
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-ink-700">{chunk.text}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
