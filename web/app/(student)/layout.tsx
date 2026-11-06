import type { ReactNode } from "react";

import { PortalShell } from "@/components/portal-shell";
import { requireRole } from "@/lib/session";

export default async function StudentLayout({ children }: { children: ReactNode }) {
  await requireRole("student");
  return <PortalShell role="student">{children}</PortalShell>;
}
