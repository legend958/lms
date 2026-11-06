import { expect, test } from "@playwright/test";

import { ACCOUNTS, api, login } from "./helpers";

interface UserRecord {
  id: string;
  email: string;
  role: string;
  status: string;
}

interface Page<T> {
  items: T[];
}

test.describe("admin portal", () => {
  test("create a user, change role, deactivate, and see it in the audit log", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const stamp = Date.now();
    const email = `phase9-e2e-${stamp}@lms.example.com`;
    let userId: string | null = null;

    await login(page, ACCOUNTS.admin);
    await expect(page).toHaveURL(/\/admin\/users$/);

    try {
      await page.getByRole("button", { name: "New user" }).click();
      const createDialog = page.getByRole("dialog");
      await expect(createDialog).toBeVisible();
      await createDialog.getByLabel("Full name").fill("Phase Nine");
      await createDialog.getByLabel("Email").fill(email);
      await createDialog.getByLabel("Role").selectOption("student");
      await createDialog.getByRole("button", { name: "Create user" }).click();

      await expect(page.getByRole("heading", { name: "Account created" })).toBeVisible();
      await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();

      await page.getByLabel("Search users").fill(email);
      const row = page.locator("tbody tr").filter({ hasText: email });
      await expect(row).toBeVisible({ timeout: 30_000 });
      await expect(row.getByText("student", { exact: true })).toBeVisible();

      const found = await api<Page<UserRecord>>(
        page,
        `/api/v1/admin/users?search=${encodeURIComponent(email)}`,
      );
      userId = found.items[0]?.id ?? null;
      expect(userId, "created user is returned by the admin API").toBeTruthy();

      await row.getByRole("button", { name: "Edit" }).click();
      const editDialog = page.getByRole("dialog");
      await expect(editDialog).toBeVisible();
      await editDialog.getByLabel("Role").selectOption("teacher");
      await editDialog.getByLabel("Status").selectOption("inactive");
      await editDialog.getByRole("button", { name: "Save changes" }).click();
      await expect(editDialog).toBeHidden();

      await expect(row.getByText("teacher", { exact: true })).toBeVisible({ timeout: 30_000 });
      await expect(row.getByText("inactive", { exact: true })).toBeVisible();

      await page.goto("/admin/audit");
      await page.getByLabel("Entity type").fill("user");
      await page.getByLabel("Entity id").fill(userId!);
      await page.getByRole("button", { name: "Apply filters" }).click();

      await expect(page.getByText("user.create", { exact: true })).toBeVisible({
        timeout: 30_000,
      });
      await expect(page.getByText("user.update", { exact: true })).toBeVisible();
    } finally {
      if (userId) {
        try {
          await api(page, `/api/v1/admin/users/${userId}`, { method: "DELETE" });
        } catch (error) {
          console.warn(`cleanup for user ${userId} failed:`, error);
        }
      }
    }
  });
});
