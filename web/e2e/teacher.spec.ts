import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { ACCOUNTS, api, apiLogin, login, poll } from "./helpers";

interface Course {
  id: string;
  code: string;
}

interface DocumentRecord {
  id: string;
  filename: string;
  status: string;
  course_id: string;
}

interface DocumentStatusRecord {
  status: string;
  stage: string;
  progress_pct: number;
  error_code: string | null;
  error_detail: string | null;
}

interface TopicRecord {
  id: string;
  title: string;
  release_state: "draft" | "released" | "scheduled";
}

test.describe("teacher portal", () => {
  test("upload a PDF, review its topics, and release them to a student", async ({
    page,
    browser,
  }) => {
    test.setTimeout(600_000);
    const filename = `phase9-e2e-${Date.now()}.pdf`;
    const pdf = await readFile(join(process.cwd(), "e2e", "fixtures", "demo.pdf"));
    let documentId: string | null = null;

    await login(page, ACCOUNTS.teacher);
    await expect(page).toHaveURL(/\/teacher\/documents$/);

    const courses = await api<Course[]>(page, "/api/v1/courses");
    const science = courses.find((course) => course.code === "SCI101");
    expect(science, "teacher1 must be enrolled in SCI101").toBeTruthy();
    await page.getByLabel("Course").selectOption(science!.id);

    try {
      await page.locator('input[type="file"]').setInputFiles({
        name: filename,
        mimeType: "application/pdf",
        buffer: pdf,
      });
      await expect(page.getByText("Uploaded", { exact: true })).toBeVisible({
        timeout: 60_000,
      });

      const documents = await api<DocumentRecord[]>(
        page,
        `/api/v1/documents?course_id=${science!.id}`,
      );
      const document = documents.find((row) => row.filename === filename);
      expect(document, "uploaded document is listed").toBeTruthy();
      documentId = document!.id;

      // Poll the lightweight status endpoint: the full document payload also
      // counts vectors and can be slow while the worker is embedding.
      const finished = await poll(
        () => api<DocumentStatusRecord>(page, `/api/v1/documents/${documentId}/status`),
        (row) => row.status === "ready" || row.status === "failed",
        { timeout: 420_000, interval: 3_000, label: "document to reach a terminal status" },
      );
      expect(
        finished.status,
        `ingestion failed: ${finished.error_code ?? ""} ${finished.error_detail ?? ""}`,
      ).toBe("ready");

      // The UI shows the ready document and links to its topics.
      await page.reload();
      const topicsLink = page.locator(`a[href="/teacher/documents/${documentId}/topics"]`);
      await expect(topicsLink).toBeVisible({ timeout: 30_000 });
      await topicsLink.click();
      await expect(page.getByRole("heading", { name: "Review topics" })).toBeVisible();

      const topics = await api<TopicRecord[]>(
        page,
        `/api/v1/documents/${documentId}/topics`,
      );
      expect(topics.length).toBeGreaterThan(0);
      expect(topics.every((topic) => topic.release_state === "draft")).toBe(true);

      await page.getByRole("button", { name: "Release all drafts" }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await dialog.getByRole("checkbox").uncheck();
      await dialog.getByRole("button", { name: "Release to students" }).click();
      await expect(page.getByText(/topics? released to students/)).toBeVisible({
        timeout: 60_000,
      });
      await expect(page.getByText("Released", { exact: true }).first()).toBeVisible();

      const released = await api<TopicRecord[]>(
        page,
        `/api/v1/documents/${documentId}/topics`,
      );
      expect(released.every((topic) => topic.release_state === "released")).toBe(true);

      // Cross-portal check: a real student now sees the released task.
      const studentContext = await browser.newContext();
      const studentPage = await studentContext.newPage();
      try {
        await login(studentPage, ACCOUNTS.studentThird);
        await studentPage.goto("/student/tasks");
        await expect(
          studentPage.getByRole("heading", { name: released[0].title, exact: true }),
        ).toBeVisible({ timeout: 30_000 });
      } finally {
        await studentContext.close();
      }
    } finally {
      // Restore the demo state even when an assertion fails midway. The teacher
      // delete is a soft delete, so use the admin content endpoint to remove
      // rows, vectors, and the stored PDF.
      if (documentId) {
        try {
          const topics = await api<TopicRecord[]>(
            page,
            `/api/v1/documents/${documentId}/topics`,
          );
          for (const topic of topics) {
            if (topic.release_state === "released") {
              await api(page, `/api/v1/topics/${topic.id}/unrelease`, { method: "POST" });
            }
          }
        } catch (error) {
          // Topics are 404 until ingestion leaves the extracting/chunking stages.
          console.warn(`topic cleanup for ${documentId} skipped:`, error);
        }

        const adminContext = await browser.newContext();
        const adminPage = await adminContext.newPage();
        try {
          await apiLogin(adminPage, ACCOUNTS.admin);
          await api(adminPage, `/api/v1/admin/content/${documentId}`, { method: "DELETE" });
        } catch (error) {
          console.warn(`document cleanup for ${documentId} failed:`, error);
        } finally {
          await adminContext.close();
        }
      }
    }
  });
});
