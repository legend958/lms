import { TeacherTopics } from "@/components/topics/teacher-topics";

export default async function TeacherTopicsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <TeacherTopics documentId={id} />;
}
