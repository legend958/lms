"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { useNotifications } from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type { Notification } from "@/lib/types";

function NotificationRow({
  notification,
  onRead,
  onNavigate,
}: {
  notification: Notification;
  onRead: (id: string) => void;
  onNavigate: () => void;
}) {
  const href = notification.payload.task_id ? "/student/tasks" : null;
  const title = notification.payload.title ?? "New task released";
  const course = notification.payload.course_title ?? notification.payload.course_code ?? null;
  const due = notification.payload.due_at ?? null;

  return (
    <li
      className={cn(
        "flex gap-3 px-4 py-3",
        notification.read ? "bg-white" : "bg-indigo-50/60",
      )}
    >
      <span
        className={cn(
          "mt-1 h-2 w-2 shrink-0 rounded-full",
          notification.read ? "bg-ink-200" : "bg-indigo-500",
        )}
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink-900">{title}</p>
        <p className="mt-0.5 text-xs text-ink-500">
          {course ? `${course} · ` : ""}
          {due ? `due ${formatDateTime(due)}` : formatDateTime(notification.created_at)}
        </p>
        <div className="mt-1.5 flex items-center gap-3">
          {href ? (
            <Link
              href={href}
              onClick={onNavigate}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
            >
              Open tasks
            </Link>
          ) : null}
          {!notification.read ? (
            <button
              type="button"
              onClick={() => onRead(notification.id)}
              className="text-xs font-medium text-ink-500 hover:text-ink-900"
            >
              Mark read
            </button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export function NotificationBell({ compact = false }: { compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { data } = useNotifications();
  const unread = data?.unread_count ?? 0;
  const items = data?.items.slice(0, 8) ?? [];

  useEffect(() => {
    if (!open) return;
    function handlePointer(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointer);
    return () => document.removeEventListener("mousedown", handlePointer);
  }, [open]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["notifications"] });

  const markRead = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/notifications/${id}/read`, { method: "POST" }),
    onSuccess: invalidate,
  });

  const markAll = useMutation({
    mutationFn: async () => {
      const unreadItems = items.filter((item) => !item.read);
      await Promise.all(
        unreadItems.map((item) =>
          apiFetch<void>(`/api/v1/notifications/${item.id}/read`, { method: "POST" }),
        ),
      );
    },
    onSuccess: invalidate,
  });

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        className={cn(
          "relative inline-flex items-center justify-center rounded-lg transition-colors",
          compact
            ? "h-9 w-9 text-ink-500 hover:bg-ink-100 hover:text-ink-900"
            : "h-9 w-9 text-ink-300 hover:bg-white/10 hover:text-white",
        )}
      >
        <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
          <path
            d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {unread > 0 ? (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          className={cn(
            "absolute z-50 w-80 overflow-hidden rounded-xl border border-ink-200 bg-white shadow-xl shadow-ink-900/10",
            compact ? "right-0 top-full mt-2" : "bottom-full right-0 mb-2",
          )}
        >
          <div className="flex items-center justify-between border-b border-ink-100 px-4 py-3">
            <p className="text-sm font-semibold text-ink-900">Notifications</p>
            {unread > 0 ? (
              <button
                type="button"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
              >
                Mark all read
              </button>
            ) : null}
          </div>
          {items.length === 0 ? (
            <div className="px-4 py-8 text-center">
              <p className="text-sm font-medium text-ink-800">You&apos;re all caught up</p>
              <p className="mt-1 text-xs text-ink-500">
                New task releases will appear here.
              </p>
            </div>
          ) : (
            <ul className="max-h-96 divide-y divide-ink-100 overflow-y-auto">
              {items.map((notification) => (
                <NotificationRow
                  key={notification.id}
                  notification={notification}
                  onRead={(id) => markRead.mutate(id)}
                  onNavigate={() => setOpen(false)}
                />
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
