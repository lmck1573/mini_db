-- P0 错误语句示例（列名拼写错误，语义分析阶段报错）
CREATE TABLE student(id INT, name VARCHAR, age INT);
SELECT id,nmae FROM student WHERE age > 18;
