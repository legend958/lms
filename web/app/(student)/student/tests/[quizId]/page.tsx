import { StudentQuizDetail } from "@/components/quizzes/student-quiz-detail";

export default async function StudentTestPage({
  params,
}: {
  params: Promise<{ quizId: string }>;
}) {
  const { quizId } = await params;
  return <StudentQuizDetail quizId={quizId} />;
}
