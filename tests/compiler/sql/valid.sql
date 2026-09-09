-- P0 正确语句示例
CREATE TABLE student(id INT, name VARCHAR, age INT);
INSERT INTO student(id,name,age) VALUES (1,'Alice',20);
INSERT INTO student VALUES (2,'Bob',17), (3,'Cindy',22);
SELECT id,name FROM student WHERE age > 18;
SELECT * FROM student;
DELETE FROM student WHERE id = 1;
