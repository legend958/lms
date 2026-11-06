"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { NotificationBell } from "@/components/notifications/notification-bell";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/feedback";
import { ApiError, logout } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Role } from "@/lib/types";
import { cn, homeForRole, initials } from "@/lib/utils";

const NAV: Record<Role, Array<{ href: string; label: string }>> = {
  student: [
    { href: "/student/dashboard", label: "Dashboard" },
    { href: "/student/tasks", label: "Tasks" },
    { href: "/student/tests", label: "Tests" },
    { href: "/student/courses", label: "My courses" },
    { href: "/student/assistant", label: "Study assistant" },
  ],
  teacher: [
    { href: "/teacher/documents", label: "Documents" },
    { href: "/teacher/quizzes", label: "Quizzes" },
    { href: "/teacher/announcements", label: "Announcements" },
    { href: "/teacher/analytics", label: "Analytics" },
  ],
  admin: [
    { href: "/admin/users", label: "Users" },
    { href: "/admin/courses", label: "Courses" },
    { href: "/admin/announcements", label: "Announcements" },
    { href: "/admin/content", label: "Content" },
    { href: "/admin/permissions", label: "Permissions" },
    { href: "/admin/audit", label: "Audit log" },
  ],
};

const ROLE_TONE = {
  student: "info",
  teacher: "indigo",
  admin: "warning",
} as const;

const SEGMENT_LABELS: Record<string, string> = {
  dashboard: "Dashboard",
  tasks: "Tasks",
  tests: "Tests",
  attempts: "Attempt",
  courses: "My courses",
  assistant: "Study assistant",
  library: "Study assistant",
  documents: "Documents",
  topics: "Topics",
  quizzes: "Quizzes",
  announcements: "Announcements",
  analytics: "Analytics",
  users: "Users",
  content: "Content",
  permissions: "Permissions",
  audit: "Audit log",
};

interface Crumb {
  href: string;
  label: string;
}

export function breadcrumbsFor(pathname: string, role: Role): Crumb[] {
  const roleLabel = role.charAt(0).toUpperCase() + role.slice(1);
  const segments = pathname.split("/").filter(Boolean).slice(1);
  const crumbs: Crumb[] = [{ href: homeForRole(role), label: roleLabel }];
  let href = `/${role}`;
  for (const segment of segments) {
    href = `${href}/${segment}`;
    const known = SEGMENT_LABELS[segment];
    crumbs.push({
      href,
      label: known ?? `#${segment.replaceAll("-", "").slice(0, 8)}`,
    });
  }
  return crumbs;
}

export function PortalShell({ role, children }: { role: Role; children: ReactNode }) {
  const pathname = usePathname();
  const { data: me, isLoading, error } = useMe();

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      window.location.assign("/login");
    }
  }, [error]);

  useEffect(() => {
    if (me && me.role !== role) {
      window.location.assign(homeForRole(me.role));
    }
  }, [me, role]);

  async function handleLogout() {
    try {
      await logout();
    } finally {
      window.location.assign("/login");
    }
  }

  if (isLoading || !me || me.role !== role) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading your workspace" />
      </div>
    );
  }

  const navigation = NAV[me.role];
  const crumbs = breadcrumbsFor(pathname, me.role);

  const navLink = (item: { href: string; label: string }, compact = false) => {
    const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
    return (
      <Link
        key={item.href}
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "rounded-lg px-3 py-2 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2",
          compact
            ? active
              ? "bg-ink-900 text-white focus-visible:outline-ink-900"
              : "text-ink-700 hover:bg-ink-100 focus-visible:outline-ink-400"
            : active
              ? "bg-white/10 text-white focus-visible:outline-white"
              : "text-ink-300 hover:bg-white/5 hover:text-white focus-visible:outline-ink-400",
        )}
      >
        {item.label}
      </Link>
    );
  };

  return (
    <div className="min-h-screen bg-ink-50">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[70] focus:rounded-lg focus:bg-white focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-ink-900 focus:shadow-lg focus:outline focus:outline-2 focus:outline-offset-2 focus:outline-indigo-600"
      >
        Skip to main content
      </a>

      <aside className="fixed inset-y-0 left-0 hidden w-64 flex-col bg-ink-950 px-4 py-6 lg:flex">
        <div className="flex items-center gap-3 px-2">
          <div
            aria-hidden="true"
            className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-400 to-violet-500 text-sm font-bold text-white shadow-lg shadow-indigo-900/40"
          >
            L
          </div>
          <div>
            <p className="text-sm font-semibold text-white">LMS Console</p>
            <p className="text-xs text-ink-300">RAG learning platform</p>
          </div>
        </div>

        <nav aria-label="Primary" className="mt-8 flex flex-1 flex-col gap-1">
          {navigation.map((item) => navLink(item))}
        </nav>

        <div className="rounded-xl bg-white/5 p-3">
          <div className="flex items-center gap-3">
            <div
              aria-hidden="true"
              className="flex h-9 w-9 items-center justify-center rounded-full bg-indigo-500/20 text-xs font-semibold text-indigo-200"
            >
              {initials(me.name)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-white">{me.name}</p>
              <p className="truncate text-xs text-ink-300">{me.email}</p>
            </div>
          </div>
          <div className="mt-3 flex items-center justify-between">
            <Badge tone={ROLE_TONE[me.role]}>{me.role}</Badge>
            <div className="flex items-center gap-1">
              <NotificationBell />
              <button
                type="button"
                onClick={handleLogout}
                className="rounded px-1 text-xs font-medium text-ink-200 transition-colors hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-ink-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden">
          <div className="flex items-center gap-2">
            <div
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-400 to-violet-500 text-xs font-bold text-white"
            >
              L
            </div>
            <span className="text-sm font-semibold text-ink-900">LMS Console</span>
          </div>
          <div className="flex items-center gap-2">
            <NotificationBell compact />
            <button
              type="button"
              onClick={handleLogout}
              className="rounded px-1 text-xs font-medium text-ink-600 hover:text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
            >
              Sign out
            </button>
          </div>
        </header>

        <nav
          aria-label="Mobile primary"
          className="flex gap-1 overflow-x-auto border-b border-ink-200 bg-white px-3 py-2 lg:hidden"
        >
          {navigation.map((item) => navLink(item, true))}
        </nav>

        <main
          id="main-content"
          tabIndex={-1}
          className="mx-auto w-full max-w-6xl px-4 py-6 focus:outline-none sm:px-6 lg:px-8 lg:py-8"
        >
          <nav aria-label="Breadcrumb" className="mb-3">
            <ol className="flex flex-wrap items-center gap-1 text-xs text-ink-600">
              {crumbs.map((crumb, index) => {
                const current = index === crumbs.length - 1;
                return (
                  <li key={`${crumb.href}-${index}`} className="flex items-center gap-1">
                    {index > 0 ? (
                      <span aria-hidden="true" className="text-ink-500">
                        /
                      </span>
                    ) : null}
                    {current ? (
                      <span aria-current="page" className="font-medium text-ink-800">
                        {crumb.label}
                      </span>
                    ) : (
                      <Link
                        href={crumb.href}
                        className="rounded underline-offset-2 hover:text-ink-900 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                      >
                        {crumb.label}
                      </Link>
                    )}
                  </li>
                );
              })}
            </ol>
          </nav>

          {children}
        </main>
      </div>
    </div>
  );
}
