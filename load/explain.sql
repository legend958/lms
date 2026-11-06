-- Hot-path query plans for Phase 9 (run against a seeded database).
--
--   docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres \
--     psql -U lms -d lms -f /dev/stdin < load/explain.sql
--
-- Every plan below is the SQL shape SQLAlchemy emits for the corresponding
-- service call. Timings are from the reference stack (small dataset), so look at
-- the access method, not the millisecond values.

\echo === Q1 student task list (/api/v1/student/tasks) ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT t.id, t.title, t.due_at, tp.status
FROM tasks t
JOIN topics tp2 ON tp2.id = t.topic_id
JOIN documents d ON d.id = tp2.document_id
JOIN courses c ON c.id = t.course_id
JOIN enrollments e
  ON e.course_id = t.course_id
 AND e.user_id = (SELECT id FROM users WHERE email = 'student01@lms.example.com')
 AND e.course_role = 'student'
LEFT JOIN task_progress tp
  ON tp.task_id = t.id
 AND tp.student_id = (SELECT id FROM users WHERE email = 'student01@lms.example.com')
WHERE t.status = 'published'
  AND t.course_id IN (SELECT course_id FROM enrollments
                      WHERE user_id = (SELECT id FROM users WHERE email = 'student01@lms.example.com'))
  AND tp2.release_state = 'released'
  AND tp2.deleted_at IS NULL
  AND d.deleted_at IS NULL
  AND c.status = 'active'
ORDER BY t.due_at ASC, t.id ASC;

\echo === Q2 completed tasks per student (teacher analytics roster) ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT p.student_id, count(p.task_id) AS completed
FROM task_progress p
JOIN tasks t ON t.id = p.task_id
JOIN topics tp ON tp.id = t.topic_id
JOIN documents d ON d.id = tp.document_id
JOIN enrollments e
  ON e.user_id = p.student_id
 AND e.course_id = (SELECT id FROM courses WHERE code = 'SCI101')
 AND e.course_role = 'student'
JOIN users u ON u.id = p.student_id AND u.status = 'active'
WHERE t.course_id = (SELECT id FROM courses WHERE code = 'SCI101')
  AND t.status = 'published'
  AND p.status = 'completed'
  AND tp.release_state = 'released'
  AND tp.deleted_at IS NULL
  AND d.deleted_at IS NULL
GROUP BY p.student_id;

\echo === Q3 average quiz score per student (teacher analytics KPI) ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT a.student_id, avg(100.0 * a.score / a.max_score) AS avg_score, count(a.id) AS attempts
FROM quiz_attempts a
JOIN quizzes q ON q.id = a.quiz_id
JOIN enrollments e
  ON e.user_id = a.student_id
 AND e.course_id = (SELECT id FROM courses WHERE code = 'SCI101')
 AND e.course_role = 'student'
JOIN users u ON u.id = a.student_id AND u.status = 'active'
WHERE q.course_id = (SELECT id FROM courses WHERE code = 'SCI101')
  AND a.submitted_at IS NOT NULL
  AND a.score IS NOT NULL
  AND a.max_score IS NOT NULL
  AND a.max_score > 0
GROUP BY a.student_id;

\echo === Q4 knowledge gaps (per-question accuracy) ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT qu.id, qu.stem, count(*) AS answers,
       avg(CASE WHEN aa.selected_option = qu.correct_option THEN 1.0 ELSE 0.0 END) AS accuracy
FROM questions qu
JOIN quizzes q ON q.id = qu.quiz_id
JOIN attempt_answers aa ON aa.question_id = qu.id
JOIN quiz_attempts a ON a.id = aa.attempt_id
WHERE q.course_id = (SELECT id FROM courses WHERE code = 'SCI101')
  AND a.submitted_at IS NOT NULL
GROUP BY qu.id, qu.stem
ORDER BY accuracy ASC, answers DESC
LIMIT 10;

\echo === Q5 admin audit viewer (entity filter, newest first) ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, action, entity_type, entity_id, created_at
FROM audit_logs
WHERE entity_type = 'user'
  AND entity_id = (SELECT id::text FROM users WHERE email = 'student01@lms.example.com')
ORDER BY created_at DESC
LIMIT 51;

\echo === Q6 student quiz list (release-gated) ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT q.id, q.title, tp.title
FROM quizzes q
JOIN topics tp ON tp.id = q.topic_id
JOIN documents d ON d.id = tp.document_id
JOIN courses c ON c.id = q.course_id
JOIN enrollments e
  ON e.course_id = q.course_id
 AND e.user_id = (SELECT id FROM users WHERE email = 'student01@lms.example.com')
 AND e.course_role = 'student'
WHERE q.course_id IN (SELECT course_id FROM enrollments
                      WHERE user_id = (SELECT id FROM users WHERE email = 'student01@lms.example.com'))
  AND q.status = 'published'
  AND tp.release_state = 'released'
  AND tp.deleted_at IS NULL
  AND d.deleted_at IS NULL
  AND c.status = 'active'
ORDER BY c.code, tp.order_index, q.id;
