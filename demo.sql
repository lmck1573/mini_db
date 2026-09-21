-- Mini-DB 端到端演示（P0：CREATE TABLE / INSERT / SELECT / DELETE）
CREATE TABLE student (id INT, name VARCHAR(32), score FLOAT);
INSERT INTO student VALUES (1, 'Alice', 90.5), (2, 'Bob', 80.0), (3, 'Carol', 85.5);
SELECT * FROM student;
SELECT id, name FROM student WHERE score > 82.0;
DELETE FROM student WHERE id = 2;
SELECT * FROM student;
