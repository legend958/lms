"use client";

import { useQuery } from "@tanstack/react-query";

import { AnnouncementComposer, type CourseOption } from "@/components/announcements/announcement-composer";
import { AnnouncementList } from "@/components/announcements/announcement-list";
import { ErrorBanner, Spinner } from "@/components/ui/feedback";
import { apiFetch } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Course } from "@/lib/types";

export function StaffAnnouncements() {
  const { data: me, isLoading } = useMe();
  const isAdmin = me?.role === "admin";

  const coursesQuery = useQuery({
    queryKey: ["courses"],
    queryFn: () => apiFetch<Course[]>("/api/v1/courses"),
    enabled: Boolean(me),
  });

  if (isLoading || coursesQuery.isLoading || !me) {
    return <Spinner label="Loading announcements" />;
  }
  if (coursesQuery.error) {
    return <ErrorBanner message="Could not load your courses." />;
  }

  const options: CourseOption[] = isAdmin
    ? (coursesQuery.data ?? []).map((course) => ({
        id: course.id,
        code: course.code,
        title: course.title,
      }))
    : me.courses
        .filter((course) => course.course_role === "teacher")
        .map((course) => ({ id: course.id, code: course.code, title: course.title }));

  return (
    <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
      <AnnouncementComposer courses={options} allowGlobal={isAdmin} />
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink-900">Published & scheduled</h2>
          <span className="text-xs text-ink-500">{options.length} course(s)</span>
        </div>
        <AnnouncementList scheduled manage />
      </div>
    </div>
  );
}
