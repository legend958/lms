import { StaffAnnouncements } from "@/components/announcements/staff-announcements";
import { PageHeader } from "@/components/ui/feedback";

export default function TeacherAnnouncementsPage() {
  return (
    <div>
      <PageHeader
        title="Announcements"
        description="Post updates to your courses. Students see them on their dashboard."
      />
      <StaffAnnouncements />
    </div>
  );
}
