"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ApiError, apiFetch } from "@/lib/api";
import type { Role, User, UserStatus } from "@/lib/types";

export function UserEditDialog({
  user,
  onClose,
}: {
  user: User | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("student");
  const [status, setStatus] = useState<UserStatus>("active");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    setName(user.name);
    setRole(user.role);
    setStatus(user.status);
    setError(null);
  }, [user]);

  const update = useMutation({
    mutationFn: () =>
      apiFetch<User>(`/api/v1/admin/users/${user?.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name, role, status }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      onClose();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not update the user.");
    },
  });

  return (
    <Modal
      open={Boolean(user)}
      onClose={onClose}
      title="Edit user"
      description={user?.email}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => update.mutate()} disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save changes"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Full name">
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Role">
            <Select value={role} onChange={(event) => setRole(event.target.value as Role)}>
              <option value="student">Student</option>
              <option value="teacher">Teacher</option>
              <option value="admin">Admin</option>
            </Select>
          </Field>
          <Field label="Status">
            <Select
              value={status}
              onChange={(event) => setStatus(event.target.value as UserStatus)}
            >
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </Select>
          </Field>
        </div>
        {error ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {error}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
