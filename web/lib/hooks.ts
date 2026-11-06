"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "./api";
import type {
  AdminContentRow,
  AnalyticsStudentDetail,
  AnalyticsStudentList,
  AnalyticsStudentSort,
  AnalyticsTopics,
  AuditLogEntry,
  CohortKpis,
  DocumentStatus,
  Me,
  NotificationList,
  Page,
  PermissionRow,
  QuizDetail,
  QuizList,
  StudentAttemptList,
  StudentAttemptResult,
  StudentAttemptState,
  StudentQuiz,
  StudentQuizDetail,
  StudentTopicView,
  SystemHealth,
} from "./types";

function withQuery(params: URLSearchParams): string {
  const suffix = params.toString();
  return suffix ? `?${suffix}` : "";
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<Me>("/api/v1/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useNotifications() {
  return useQuery({
    queryKey: ["notifications"],
    queryFn: () => apiFetch<NotificationList>("/api/v1/notifications"),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
}

export function useTeacherQuizzes(courseId?: string) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  const suffix = params.toString();
  return useQuery({
    queryKey: ["teacher-quizzes", courseId ?? "all"],
    queryFn: () => apiFetch<QuizList>(`/api/v1/teacher/quizzes${suffix ? `?${suffix}` : ""}`),
  });
}

export function useTeacherQuiz(quizId: string) {
  return useQuery({
    queryKey: ["teacher-quiz", quizId],
    queryFn: () => apiFetch<QuizDetail>(`/api/v1/teacher/quizzes/${quizId}`),
    // Generation is asynchronous: poll while the worker is still working.
    refetchInterval: (query) => {
      const status = query.state.data?.generation_status;
      return status === "queued" || status === "generating" ? 2_000 : false;
    },
  });
}

export function useStudentQuizzes(courseId?: string) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  const suffix = params.toString();
  return useQuery({
    queryKey: ["student-quizzes", courseId ?? "all"],
    queryFn: () => apiFetch<StudentQuiz[]>(`/api/v1/student/quizzes${suffix ? `?${suffix}` : ""}`),
  });
}

export function useStudentQuiz(quizId: string) {
  return useQuery({
    queryKey: ["student-quiz", quizId],
    queryFn: () => apiFetch<StudentQuizDetail>(`/api/v1/student/quizzes/${quizId}`),
  });
}

export function useStudentAttempt(attemptId: string) {
  return useQuery({
    queryKey: ["student-attempt", attemptId],
    queryFn: () => apiFetch<StudentAttemptState>(`/api/v1/student/attempts/${attemptId}`),
  });
}

export function useStudentAttempts(quizId?: string) {
  const params = new URLSearchParams();
  if (quizId) params.set("quiz_id", quizId);
  const suffix = params.toString();
  return useQuery({
    queryKey: ["student-attempts", quizId ?? "all"],
    queryFn: () =>
      apiFetch<StudentAttemptList>(`/api/v1/student/attempts${suffix ? `?${suffix}` : ""}`),
  });
}

export function useStudentAttemptResult(attemptId: string, enabled = true) {
  return useQuery({
    queryKey: ["student-attempt-result", attemptId],
    queryFn: () => apiFetch<StudentAttemptResult>(`/api/v1/student/attempts/${attemptId}/result`),
    enabled,
  });
}

export function useStudentTopics(courseId?: string) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  const suffix = params.toString();
  return useQuery({
    queryKey: ["student-topics", courseId ?? "all"],
    queryFn: () =>
      apiFetch<StudentTopicView[]>(`/api/v1/student/topics${suffix ? `?${suffix}` : ""}`),
    staleTime: 60_000,
  });
}

export function useAnalyticsKpis(courseId?: string, week?: number) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  if (week !== undefined) params.set("week", String(week));
  return useQuery({
    queryKey: ["analytics-kpis", courseId ?? "none", week ?? "all"],
    queryFn: () => apiFetch<CohortKpis>(`/api/v1/teacher/analytics${withQuery(params)}`),
    enabled: Boolean(courseId),
    staleTime: 30_000,
  });
}

export function useAnalyticsStudents(
  courseId?: string,
  week?: number,
  sort: AnalyticsStudentSort = "completion",
) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  if (week !== undefined) params.set("week", String(week));
  params.set("sort", sort);
  params.set("limit", "100");
  return useQuery({
    queryKey: ["analytics-students", courseId ?? "none", week ?? "all", sort],
    queryFn: () => apiFetch<AnalyticsStudentList>(`/api/v1/teacher/analytics/students${withQuery(params)}`),
    enabled: Boolean(courseId),
    staleTime: 30_000,
  });
}

export function useAnalyticsTopics(courseId?: string) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  return useQuery({
    queryKey: ["analytics-topics", courseId ?? "none"],
    queryFn: () => apiFetch<AnalyticsTopics>(`/api/v1/teacher/analytics/topics${withQuery(params)}`),
    enabled: Boolean(courseId),
    staleTime: 30_000,
  });
}

export function useAnalyticsStudent(courseId?: string, studentId?: string) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  return useQuery({
    queryKey: ["analytics-student", courseId ?? "none", studentId ?? "none"],
    queryFn: () =>
      apiFetch<AnalyticsStudentDetail>(
        `/api/v1/teacher/analytics/students/${studentId}${withQuery(params)}`,
      ),
    enabled: Boolean(courseId && studentId),
    staleTime: 30_000,
  });
}

export function useAdminContent(courseId?: string, status?: DocumentStatus) {
  const params = new URLSearchParams();
  if (courseId) params.set("course_id", courseId);
  if (status) params.set("status", status);
  return useQuery({
    queryKey: ["admin-content", courseId ?? "all", status ?? "all"],
    queryFn: () => apiFetch<AdminContentRow[]>(`/api/v1/admin/content${withQuery(params)}`),
    staleTime: 15_000,
  });
}

export function useAdminPermissions() {
  return useQuery({
    queryKey: ["admin-permissions"],
    queryFn: () => apiFetch<PermissionRow[]>("/api/v1/admin/permissions"),
    staleTime: 30_000,
  });
}

export interface AdminAuditFilters {
  actorId?: string;
  action?: string;
  entityType?: string;
  entityId?: string;
  from?: string;
  to?: string;
  cursor?: string;
}

export function useAdminAuditLogs(filters: AdminAuditFilters) {
  const params = new URLSearchParams({ limit: "50" });
  if (filters.actorId) params.set("actor_id", filters.actorId);
  if (filters.action) params.set("action", filters.action);
  if (filters.entityType) params.set("entity_type", filters.entityType);
  if (filters.entityId) params.set("entity_id", filters.entityId);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.cursor) params.set("cursor", filters.cursor);
  return useQuery({
    queryKey: ["admin-audit", filters],
    queryFn: () =>
      apiFetch<Page<AuditLogEntry>>(`/api/v1/admin/audit-logs?${params.toString()}`),
    staleTime: 10_000,
  });
}

export function useSystemHealth() {
  return useQuery({
    queryKey: ["admin-health"],
    queryFn: () => apiFetch<SystemHealth>("/api/v1/admin/system/health"),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });
}
