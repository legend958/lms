export type Role = "student" | "teacher" | "admin";
export type UserStatus = "active" | "inactive" | "deleted";
export type CourseRole = "student" | "teacher";
export type CourseStatus = "active" | "archived";
export type TopicSource = "llm" | "fallback";
export type TopicReleaseState = "draft" | "released" | "scheduled";
export type TaskStatus = "draft" | "published" | "archived";
export type TaskProgressStatus = "completed" | "incomplete";
export type StudentTaskFilter = "all" | "incomplete" | "completed";
export type StudentTaskSort = "due_at" | "released_at";
export type NotificationType = "task_released";
export type QuizStatus = "draft" | "published" | "archived";
export type QuizGenerationStatus = "queued" | "generating" | "ready" | "failed";
export type QuestionDifficulty = "easy" | "medium" | "hard";
export type DocumentStatus =
  | "queued"
  | "extracting"
  | "chunking"
  | "embedding"
  | "indexed"
  | "topic_extraction"
  | "ready"
  | "failed";

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  status: UserStatus;
  created_at: string;
}

export interface Membership {
  id: string;
  code: string;
  title: string;
  course_role: CourseRole;
}

export interface Me extends User {
  courses: Membership[];
}

export interface Course {
  id: string;
  code: string;
  title: string;
  term: string | null;
  description: string | null;
  status: CourseStatus;
  created_by: string;
  created_at: string;
}

export interface Enrollment {
  user_id: string;
  name: string;
  email: string;
  course_role: CourseRole;
}

export interface Announcement {
  id: string;
  title: string;
  body: string;
  course_id: string | null;
  course_title: string | null;
  author_id: string;
  author_name: string;
  publish_at: string;
  expires_at: string | null;
  read: boolean;
  created_at: string;
}

export interface DocumentRecord {
  id: string;
  course_id: string;
  course_code: string;
  course_title: string;
  uploaded_by: string;
  uploader_name: string;
  filename: string;
  checksum: string;
  size_bytes: number;
  page_count: number | null;
  status: DocumentStatus;
  error_code: string | null;
  error_detail: string | null;
  pipeline_version: number;
  topic_count: number;
  topics_source: TopicSource | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface TopicRecord {
  id: string;
  document_id: string;
  course_id: string;
  title: string;
  summary: string;
  page_start: number;
  page_end: number;
  order_index: number;
  chunk_count: number;
  release_state: TopicReleaseState;
  released_at: string | null;
  needs_review: boolean;
  task_id: string | null;
  task_status: TaskStatus | null;
  created_at: string;
  updated_at: string;
}

export interface TopicReleaseResult {
  topic_id: string;
  task_id: string;
  task_status: TaskStatus;
  released_at: string;
  chroma_synced: boolean;
  reconcile_queued: boolean;
  quiz_id: string | null;
  quiz_queued: boolean;
}

export interface StudentTopicView {
  id: string;
  course_id: string;
  document_id: string;
  title: string;
  summary: string;
  page_start: number;
  page_end: number;
  chunk_count: number;
}

export interface StudentChunkView {
  id: string;
  text: string;
  page_start: number;
  page_end: number;
  section_title: string | null;
}

export interface StudentTopicPreview {
  topic: StudentTopicView;
  chunks: StudentChunkView[];
}

export interface StudentTaskCourse {
  id: string;
  code: string;
  title: string;
}

export interface StudentTask {
  id: string;
  topic_id: string;
  title: string;
  description: string;
  course: StudentTaskCourse;
  week_no: number;
  due_at: string;
  status: TaskProgressStatus;
  overdue: boolean;
  completed_at: string | null;
}

export interface StudentTaskList {
  items: StudentTask[];
  next_cursor: string | null;
}

export interface StudentTaskUpdateResult {
  status: TaskProgressStatus;
  completed_at: string | null;
}

export interface StudentTaskCounts {
  pending: number;
  completed: number;
  overdue: number;
}

export interface StudentDashboard {
  counts: StudentTaskCounts;
  due_soon: StudentTask[];
  announcements: Announcement[];
}

export interface NotificationPayload {
  task_id?: string;
  topic_id?: string;
  course_id?: string;
  course_code?: string | null;
  course_title?: string | null;
  title?: string;
  week_no?: number;
  due_at?: string | null;
  [key: string]: unknown;
}

export interface Notification {
  id: string;
  type: NotificationType;
  payload: NotificationPayload;
  read: boolean;
  read_at: string | null;
  created_at: string;
}

export interface NotificationList {
  items: Notification[];
  unread_count: number;
}

export interface DocumentUploadResult {
  document_id: string;
  status: DocumentStatus;
}

export interface DocumentStatusInfo {
  document_id: string;
  status: DocumentStatus;
  stage: string;
  progress_pct: number;
  attempts: number;
  error_code: string | null;
  error_detail: string | null;
  started_at: string | null;
  finished_at: string | null;
  chunk_count: number;
}

export interface QuizCourseRef {
  id: string;
  code: string;
  title: string;
}

export interface TeacherQuestion {
  id: string;
  quiz_id: string;
  stem: string;
  options: string[];
  correct_option: number;
  explanation: string;
  difficulty: QuestionDifficulty;
  source_chunk_id: string;
  approved: boolean;
  order_index: number;
}

export interface QuizRecord {
  id: string;
  topic_id: string;
  course_id: string;
  topic_title: string;
  title: string;
  pass_threshold: number;
  attempt_limit: number;
  status: QuizStatus;
  auto_approve: boolean;
  needs_review: boolean;
  generation_status: QuizGenerationStatus;
  generation_error: string | null;
  question_count: number;
  approved_count: number;
  attempt_count: number;
  created_at: string;
  updated_at: string;
}

export interface QuizDetail extends QuizRecord {
  questions: TeacherQuestion[];
}

export interface QuizList {
  items: QuizRecord[];
}

export interface StudentQuestion {
  id: string;
  stem: string;
  options: string[];
  order_index: number;
}

export interface StudentQuiz {
  id: string;
  topic_id: string;
  course: QuizCourseRef;
  title: string;
  question_count: number;
  pass_threshold: number;
  attempt_limit: number;
  attempts_used: number;
  best_score: number | null;
  open_attempt_id: string | null;
}

export interface StudentQuizDetail {
  quiz: StudentQuiz;
  questions: StudentQuestion[];
}

export interface AnswerInput {
  question_id: string;
  selected_option: number;
  time_spent_ms?: number | null;
}

export interface AttemptAnswer {
  question_id: string;
  selected_option: number | null;
  time_spent_ms: number | null;
}

export interface StudentAttemptState {
  attempt_id: string;
  quiz: StudentQuiz;
  started_at: string;
  submitted_at: string | null;
  questions: StudentQuestion[];
  answers: AttemptAnswer[];
}

export interface AttemptSaveResult {
  saved: number;
  answered: number;
  total: number;
}

export interface AttemptReviewItem {
  question_id: string;
  stem: string;
  options: string[];
  selected_option: number | null;
  correct_option: number;
  is_correct: boolean;
  explanation: string;
  source_chunk_id: string;
}

export interface StudentAttemptResult {
  attempt_id: string;
  quiz_id: string;
  quiz_title: string;
  score: number;
  max_score: number;
  percentage: number;
  passed: boolean;
  pass_threshold: number;
  submitted_at: string;
  review: AttemptReviewItem[];
}

export interface StudentAttemptSummary {
  id: string;
  quiz_id: string;
  quiz_title: string;
  course: QuizCourseRef;
  started_at: string;
  submitted_at: string | null;
  score: number | null;
  max_score: number | null;
  percentage: number | null;
  passed: boolean;
  open: boolean;
}

export interface StudentAttemptList {
  items: StudentAttemptSummary[];
  next_cursor: string | null;
}

export interface AssistantCitation {
  document_id: string;
  title: string;
  page: number;
}

export type AnalyticsStudentSort =
  | "completion"
  | "name"
  | "last_active"
  | "avg_score"
  | "days_active"
  | "at_risk";

export interface CohortKpis {
  students: number;
  completion_rate: number;
  pending: number;
  completed: number;
  avg_score: number | null;
  active_today: number;
}

export interface AnalyticsStudentRow {
  id: string;
  name: string;
  email: string;
  completion_rate: number;
  completed: number;
  pending: number;
  last_active_at: string | null;
  days_active: number;
  avg_score: number | null;
  at_risk: boolean;
  weak_topic_count: number;
}

export interface AnalyticsStudentList {
  items: AnalyticsStudentRow[];
  next_cursor: string | null;
}

export interface TopicAnalytics {
  id: string;
  title: string;
  document_id: string;
  document_title: string;
  release_state: TopicReleaseState;
  students: number;
  completed: number;
  pending: number;
  completion_rate: number;
  attempt_count: number;
  avg_score: number | null;
  question_accuracy: number | null;
}

export interface KnowledgeGap {
  kind: "topic" | "question";
  topic_id: string;
  topic_title: string;
  question_id: string | null;
  question_stem: string | null;
  accuracy: number;
  answers: number;
}

export interface AnalyticsTopics {
  items: TopicAnalytics[];
  gaps: KnowledgeGap[];
}

export interface AnalyticsStudentRef {
  id: string;
  name: string;
  email: string;
}

export interface AnalyticsStudentSummary {
  completion_rate: number;
  completed: number;
  pending: number;
  avg_score: number | null;
  days_active: number;
  last_active_at: string | null;
  at_risk: boolean;
  weak_topic_count: number;
}

export interface AnalyticsTaskHistoryItem {
  task_id: string;
  title: string;
  week_no: number;
  due_at: string;
  status: TaskProgressStatus;
  completed_at: string | null;
}

export interface AnalyticsAttemptItem {
  attempt_id: string;
  quiz_id: string;
  quiz_title: string;
  topic_title: string;
  started_at: string;
  submitted_at: string | null;
  score: number | null;
  max_score: number | null;
  percentage: number | null;
  duration_seconds: number | null;
}

export interface AnalyticsWeakQuestion {
  question_id: string;
  topic_id: string;
  topic_title: string;
  stem: string;
  selected_option: number | null;
  correct_option: number;
  explanation: string;
  source_document: string | null;
  source_page: number | null;
}

export interface AnalyticsStudentDetail {
  student: AnalyticsStudentRef;
  summary: AnalyticsStudentSummary;
  tasks: AnalyticsTaskHistoryItem[];
  attempts: AnalyticsAttemptItem[];
  weakest_questions: AnalyticsWeakQuestion[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  request_id: string;
}

export interface AdminContentRow {
  id: string;
  course_id: string;
  course_code: string;
  course_title: string;
  uploaded_by: string;
  uploader_name: string;
  filename: string;
  size_bytes: number;
  page_count: number | null;
  status: DocumentStatus;
  error_code: string | null;
  topic_count: number;
  released_topics: number;
  chunk_count: number;
  topics_source: TopicSource | null;
  created_at: string;
  updated_at: string;
}

export interface ContentAction {
  document_id: string;
  status: DocumentStatus;
}

export interface ContentDownload {
  document_id: string;
  filename: string;
  url: string;
  expires_in: number;
}

export interface AuditLogEntry {
  id: string;
  actor_id: string | null;
  actor_name: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  ip: string | null;
  request_id: string | null;
  created_at: string;
}

export interface PermissionRow {
  key: string;
  role: Role;
  enabled: boolean;
  affected_users: number;
}

export interface ImportRowResult {
  row_number: number;
  email: string | null;
  status: "created" | "skipped" | "error";
  reason: string | null;
  user_id: string | null;
  temporary_password: string | null;
}

export interface ImportReport {
  mode: "atomic" | "partial";
  total: number;
  created: number;
  skipped: number;
  failed: number;
  rows: ImportRowResult[];
}

export interface SystemHealth {
  status: "ok" | "degraded";
  checks: Record<string, string>;
  queue_depth: number | null;
  last_ingestion_failure: {
    document_id: string;
    filename: string;
    course_code: string;
    error_code: string | null;
    error_detail: string | null;
    updated_at: string;
  } | null;
  generated_at: string;
}
