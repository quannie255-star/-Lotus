-- 只在数据目录为空时执行（PostgreSQL 官方镜像的初始化约定）。
-- 一个实例分两个逻辑库：metastore（Hive 元数据）与 airflow（调度元数据）。
-- 分开的好处：备份/恢复互不牵连，误删 airflow 库不会带走表定义，
-- 也让"元数据 vs 数据"这条边界在库级别上可见。

CREATE DATABASE airflow;
