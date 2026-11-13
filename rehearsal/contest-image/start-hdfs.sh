#!/usr/bin/env bash
# 容器啟動：以 player 身分起單機 NameNode + DataNode，第一次啟動時把種子資料放進 HDFS /data/contest/
set -uo pipefail
mkdir -p "$HADOOP_LOG_DIR" "$HADOOP_PID_DIR"

if [ ! -d /var/lib/hdfs/name/current ]; then
    hdfs namenode -format -nonInteractive -force > /var/lib/hdfs/format.log 2>&1
fi
hdfs --daemon start namenode
hdfs --daemon start datanode
hdfs dfsadmin -safemode wait
for _ in $(seq 1 90); do
    hdfs dfsadmin -report 2>/dev/null | grep -q 'Live datanodes (1)' && break
    sleep 1
done

if [ -d /var/lib/hdfs/seed ] && ! hdfs dfs -test -d /data/contest; then
    hdfs dfs -mkdir -p /data/contest \
        && hdfs dfs -put /var/lib/hdfs/seed/* /data/contest/ \
        && rm -rf /var/lib/hdfs/seed   # 種子資料只留在 HDFS 上
fi
hdfs dfs -ls -R /data/contest > /var/lib/hdfs/READY 2>&1
echo "HDFS ready"
exec sleep infinity
