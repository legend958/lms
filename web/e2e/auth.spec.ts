import { expect, test } from "@playwright/test";

import { ACCOUNTS, HOME, login, logout } from "./helpers";

test.describe("authentication and portal segregation", () => {
  test("unauthenticated visitors are sent to sign in", async ({ page }) => {
    await page.goto("/student/dashboard");
    await expect(page).toHaveURL(/\/login\?next=%2Fstudent%2Fdashboard/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("student lands in the student portal and cannot open other portals", async ({ page }) => {
    await login(page, ACCOUNTS.studentThird);
    await expect(page).toHaveURL(new RegExp(`${HOME.student}$`));
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Welcome back");

    await page.goto("/teacher/documents");
    await expect(page).toHaveURL(new RegExp(`${HOME.student}$`));

    await page.goto("/admin/users");
    await expect(page).toHaveURL(new RegExp(`${HOME.student}$`));
  });

  test("teacher and admin are confined to their own portals", async ({ browser }) => {
    const teacherContext = await browser.newContext();
    const teacherPage = await teacherContext.newPage();
    await login(teacherPage, ACCOUNTS.teacher);
    await expect(teacherPage).toHaveURL(new RegExp(`${HOME.teacher}$`));
    await teacherPage.goto("/student/dashboard");
    await expect(teacherPage).toHaveURL(new RegExp(`${HOME.teacher}$`));
    await teacherContext.close();

    const adminContext = await browser.newContext();
    const adminPage = await adminContext.newPage();
    await login(adminPage, ACCOUNTS.admin);
    await expect(adminPage).toHaveURL(new RegExp(`${HOME.admin}$`));
    await adminPage.goto("/teacher/analytics");
    await expect(adminPage).toHaveURL(new RegExp(`${HOME.admin}$`));
    await adminContext.close();
  });

  test("signing out clears the portal session", async ({ page }) => {
    await login(page, ACCOUNTS.studentThird);
    await logout(page);
    await expect(page).toHaveURL(/\/login/);

    await page.goto("/student/tasks");
    await expect(page).toHaveURL(/\/login\?next=%2Fstudent%2Ftasks/);
  });
});
