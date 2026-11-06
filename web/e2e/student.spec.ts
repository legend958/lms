import { expect, test } from "@playwright/test";

import { ACCOUNTS, login } from "./helpers";

test.describe("student portal", () => {
  test("dashboard, task completion, and the grounded assistant", async ({ page }) => {
    test.setTimeout(180_000);
    await login(page, ACCOUNTS.studentAlt);
    await expect(page).toHaveURL(/\/student\/dashboard$/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Welcome back");

    // Task completion is server-side and must survive a reload.
    await page.goto("/student/tasks");
    await expect(page.getByRole("heading", { name: "Tasks", level: 1 })).toBeVisible();
    const incomplete = page.locator('[role="checkbox"][aria-checked="false"]').first();
    await expect(incomplete).toBeVisible();
    const label = await incomplete.getAttribute("aria-label");
    await expect(label).toBeTruthy();
    const taskTitle = label!.replace(/^Mark /, "").replace(/ (in)?complete$/, "");
    const task = page.locator("article").filter({ hasText: taskTitle });

    await incomplete.click();
    await expect(task.getByRole("checkbox")).toHaveAttribute("aria-checked", "true");
    await page.reload();
    await expect(
      page.locator("article").filter({ hasText: taskTitle }).getByRole("checkbox"),
    ).toHaveAttribute("aria-checked", "true");

    // Restore the seed state so the demo and other specs stay deterministic.
    await page
      .locator("article")
      .filter({ hasText: taskTitle })
      .getByRole("checkbox")
      .click();
    await expect(
      page.locator("article").filter({ hasText: taskTitle }).getByRole("checkbox"),
    ).toHaveAttribute("aria-checked", "false");

    // The assistant answers only from released material and cites pages or refuses.
    await page.goto("/student/assistant");
    await expect(page.getByRole("heading", { name: "Study assistant" })).toBeVisible();
    await page.getByLabel("Your question").fill("What does chlorophyll do in photosynthesis?");
    await page.getByRole("button", { name: "Send" }).click();

    const sources = page.getByText("Sources", { exact: true });
    const refusal = page.getByText("Not covered", { exact: true });
    await expect(sources.or(refusal).first()).toBeVisible({ timeout: 120_000 });

    // Every grounded answer keeps its citation chips attached to the message.
    if (await sources.isVisible()) {
      const citation = page.locator("span[title*='page']").first();
      await expect(citation).toBeVisible();
    }
  });

  test("student can open the released quizzes list", async ({ page }) => {
    await login(page, ACCOUNTS.studentAlt);
    await page.goto("/student/tests");
    await expect(page.getByRole("heading", { name: "Tests", level: 1 })).toBeVisible();
    await expect(page.getByText(/available/).first()).toBeVisible();
    await expect(page.getByText("Chloroplast Structure").first()).toBeVisible();
  });
});
