-- 模块一扩展能力演示（多表连接 / 分组聚合 / 排序 / EXPLAIN）
-- 运行：python -m src.main --file tests/compiler/sql/advanced.sql

CREATE TABLE student(id INT, name VARCHAR(20), age INT, dept VARCHAR(20));
CREATE TABLE score(sid INT, course VARCHAR(20), score INT);

-- 1) 内连接 + 限定列 + 过滤
SELECT s.name, c.course, c.score
FROM student s JOIN score c ON s.id = c.sid
WHERE c.score > 90;

-- 2) 左连接（无匹配的成绩行补 NULL）
SELECT s.name, c.course
FROM student s LEFT JOIN score c ON s.id = c.sid;

-- 3) 逗号隐式连接
SELECT student.name, score.course
FROM student, score
WHERE student.id = score.sid;

-- 4) 分组聚合 + HAVING + 排序
SELECT dept, COUNT(*) AS cnt, AVG(age) AS avg_age
FROM student
GROUP BY dept
HAVING COUNT(*) > 1
ORDER BY cnt DESC, dept ASC;

-- 5) 全局聚合（无 GROUP BY）
SELECT COUNT(*), MAX(score), MIN(score), SUM(score) FROM score;

-- 6) 表达式投影 + 排序 + 序号位置
SELECT name, age + 1 AS next_age FROM student ORDER BY 2 DESC;

-- 7) EXPLAIN：只编译输出计划，不执行
EXPLAIN SELECT s.name FROM student s JOIN score c ON s.id = c.sid WHERE c.score > 60;
