import type { ReactNode } from "react";

import { PortalShell } from "@/components/portal-shell";
import { requireRole } from "@/lib/session";

export default async function TeacherLayout({ children }: { children: ReactNode }) {
  await requireRole("teacher");
  return <PortalShell role="teacher">{children}</PortalShell>;
}
