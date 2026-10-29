# 官方 Spark 镜像不带 S3A 的 jar，第一次访问 s3a:// 就会
#   java.lang.RuntimeException: java.lang.ClassNotFoundException: Class org.apache.hadoop.fs.s3a.S3AFileSystem not found
# 所以在这里把两个 jar 预置进 /opt/spark/jars。
#
# 版本必须和镜像内置的 Hadoop 对齐：Spark 3.5.x 捆绑 Hadoop 3.3.4，
# 因此 hadoop-aws 用 3.3.4，它声明的 aws-java-sdk-bundle 是 1.12.262
# （见 hadoop-aws-3.3.4.pom 的依赖声明）。
# 若开启 HTTPS（ssl.enabled=true），还需 org.wildfly.openssl:wildfly-openssl:1.1.3.Final。
FROM apache/spark:3.5.3

USER root
ADD https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar /opt/spark/jars/
ADD https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar /opt/spark/jars/
# Docker 对远程 URL 的 ADD 默认落盘为 root:600，而镜像以 USER spark 运行——
# 不 chmod 的话 jar "存在但不可读"，JVM 直接 ClassNotFound（坑详见 dev-lessons #004）。
RUN chmod 0644 /opt/spark/jars/hadoop-aws-3.3.4.jar /opt/spark/jars/aws-java-sdk-bundle-1.12.262.jar
USER spark
