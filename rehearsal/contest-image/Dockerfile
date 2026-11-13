# 模擬比賽現場：Ubuntu 24.04 + Java + Hadoop 3（單機 HDFS），沒有 git / curl / python3，預設使用者 player（有 sudo）
# 建置（在 rehearsal/ 執行，先準備 cache/ 和 fakedata/seed/，見 REPORT-2026-11-13.md）：
#   docker build -f contest-image/Dockerfile -t elephant-contest .

FROM ubuntu:24.04 AS builder
COPY cache/hadoop-3.4.1-lean.tar.gz /tmp/
RUN tar -xzf /tmp/hadoop-3.4.1-lean.tar.gz -C /opt \
 && mv /opt/hadoop-3.4.1 /opt/hadoop \
 && rm -rf /opt/hadoop/share/doc

FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-11-jre-headless sudo procps \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/hadoop /opt/hadoop
ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64 \
    HADOOP_HOME=/opt/hadoop \
    HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop \
    HADOOP_LOG_DIR=/var/lib/hdfs/logs \
    HADOOP_PID_DIR=/var/lib/hdfs/pids
ENV PATH=/opt/hadoop/bin:/opt/hadoop/sbin:$PATH
COPY contest-image/core-site.xml contest-image/hdfs-site.xml /opt/hadoop/etc/hadoop/
RUN echo "export JAVA_HOME=$JAVA_HOME" >> /opt/hadoop/etc/hadoop/hadoop-env.sh \
 && printf 'export JAVA_HOME=%s\nexport HADOOP_HOME=/opt/hadoop\n' "$JAVA_HOME" > /etc/profile.d/hadoop.sh

RUN useradd -m -s /bin/bash player \
 && echo 'player ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/player && chmod 440 /etc/sudoers.d/player \
 && mkdir -p /var/lib/hdfs && chown player:player /var/lib/hdfs

# 彩排計時用：互動 shell 的 history 帶時間戳、每個指令立刻寫檔
RUN printf '\nexport HISTTIMEFORMAT="%%F %%T "\nPROMPT_COMMAND="history -a${PROMPT_COMMAND:+; $PROMPT_COMMAND}"\n' >> /etc/bash.bashrc

COPY --chown=player:player fakedata/seed /var/lib/hdfs/seed
COPY --chmod=755 contest-image/start-hdfs.sh /usr/local/bin/start-hdfs.sh

USER player
WORKDIR /home/player
CMD ["/usr/local/bin/start-hdfs.sh"]
