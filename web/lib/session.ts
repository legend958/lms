import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import type { Role } from "@/lib/types";
import { homeForRole } from "@/lib/utils";

const ROLES: readonly Role[] = ["student", "teacher", "admin"];
export const ROLE_COOKIE = "lms_role";

export function isRole(value: string | undefined): value is Role {
  return value !== undefined && (ROLES as readonly string[]).includes(value);
}

/**
 * Server-side portal guard.
 *
 * The API remains authoritative for every request; this only prevents a
 * browser from rendering another portal's shell. The role cookie is set by the
 * API on login and cleared on logout. If it is missing we send the visitor to
 * the login page, and if it names a different portal we send them to their own
 * home instead of rendering a cross-portal page.
 */
export async function requireRole(expected: Role): Promise<Role> {
  const store = await cookies();
  const role = store.get(ROLE_COOKIE)?.value;
  if (!isRole(role)) {
    redirect("/login");
  }
  if (role !== expected) {
    redirect(homeForRole(role));
  }
  return role;
}
