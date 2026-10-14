#!/bin/bash
# kill_port.sh - kill 占用指定端口的进程
# 用法: ./kill_port.sh 8080

if [ $# -eq 0 ]; then
    echo "用法: $0 <端口> [端口2 ...]"
    exit 1
fi

for port in "$@"; do
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        echo ">>> 非法端口: $port （跳过）"
        continue
    fi

    # 找占用该端口的 PID
    pids=$(lsof -t -i:"$port" 2>/dev/null)

    if [ -z "$pids" ]; then
        echo ">>> 端口 $port 未被占用"
        continue
    fi

    echo ">>> 端口 $port 被以下进程占用: $pids"
    for pid in $pids; do
        pname=$(ps -p "$pid" -o comm= 2>/dev/null)
        echo "    kill PID $pid ($pname)"
        kill -TERM "$pid" 2>/dev/null
    done
done