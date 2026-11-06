import { StudentAttemptRunner } from "@/components/quizzes/student-attempt-runner";

export default async function StudentAttemptPage({
  params,
}: {
  params: Promise<{ attemptId: string }>;
}) {
  const { attemptId } = await params;
  return <StudentAttemptRunner attemptId={attemptId} />;
}
