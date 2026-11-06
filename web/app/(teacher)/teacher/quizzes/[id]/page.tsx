import { TeacherQuizReview } from "@/components/quizzes/teacher-quiz-review";

export default async function TeacherQuizReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <TeacherQuizReview quizId={id} />;
}
