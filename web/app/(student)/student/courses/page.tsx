"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, PageHeader, Spinner } from "@/components/ui/feedback";
import { useMe } from "@/lib/hooks";

export default function StudentCoursesPage() {
  const { data: me, isLoading } = useMe();

  return (
    <div>
      <PageHeader
        title="My courses"
        description="Every course you are enrolled in. Course content is released by your teacher."
      />

      {isLoading ? <Spinner /> : null}

      {!isLoading && !me?.courses.length ? (
        <EmptyState
          title="No enrollments"
          description="An administrator can add you to a course from the admin portal."
        />
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        {me?.courses.map((course) => (
          <Card key={course.id}>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>{course.title}</CardTitle>
              <Badge tone="info">{course.code}</Badge>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-ink-600">
                You are enrolled as a {course.course_role}. Documents, topics, and the AI
                library for this course will appear here as your teacher publishes them.
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
