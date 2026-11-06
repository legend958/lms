import { StaffAnnouncements } from "@/components/announcements/staff-announcements";
import { PageHeader } from "@/components/ui/feedback";

export default function AdminAnnouncementsPage() {
  return (
    <div>
      <PageHeader
        title="Announcements"
        description="Broadcast institution-wide notices or post to a specific course."
      />
      <StaffAnnouncements />
    </div>
  );
}
