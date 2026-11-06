import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { homeForRole } from "@/lib/utils";

export default async function HomePage() {
  const role = (await cookies()).get("lms_role")?.value;
  redirect(role ? homeForRole(role) : "/login");
}
