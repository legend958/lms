import type { ReactNode } from "react";

import { PortalShell } from "@/components/portal-shell";
import { requireRole } from "@/lib/session";

export default async function AdminLayout({ children }: { children: ReactNode }) {
  await requireRole("admin");
  return <PortalShell role="admin">{children}</PortalShell>;
}
