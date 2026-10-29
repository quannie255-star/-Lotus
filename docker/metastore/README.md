# docker/metastore 依赖 jar 获取说明

本目录下的 `.jar` **不进 git**（`.gitignore` 已忽略 `docker/**/*.jar`），
boot 前需手工下载一次放到这里。原因：`aws-java-sdk-bundle` 单文件 296MB，
提交进 git 会把仓库体积撑爆，且这些是"按镜像 Hadoop 版本对号入座的补丁"，不该被版本控制。

## 为什么是这三个

`apache/hive:4.0.0` 镜像内置 Hadoop 3.3.6，但**不带**：
1. PostgreSQL JDBC 驱动（Metastore 要连 postgres）；
2. `hadoop-aws`（S3A 文件系统实现）；
3. `aws-java-sdk-bundle`（`hadoop-aws` 的运行时依赖）。

版本必须和镜像内置 Hadoop 对齐：**3.3.6**。与 Spark 侧（Hadoop 3.3.4）各对各的，
不要混用。

## 下载命令（Maven Central）

```bash
cd docker/metastore

curl -fLO https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar
curl -fLO https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.6/hadoop-aws-3.3.6.jar
curl -fLO https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.367/aws-java-sdk-bundle-1.12.367.jar
```

## 挂载映射（见 docker-compose.yml 的 metastore 服务）

| 文件 | 容器内路径 |
|---|---|
| `postgresql-42.7.4.jar` | `/opt/hive/lib/postgresql.jar` |
| `hadoop-aws-3.3.6.jar` | `/opt/hadoop/share/hadoop/common/lib/hadoop-aws-3.3.6.jar` |
| `aws-java-sdk-bundle-1.12.367.jar` | `/opt/hadoop/share/hadoop/common/lib/aws-java-sdk-bundle-1.12.367.jar` |
