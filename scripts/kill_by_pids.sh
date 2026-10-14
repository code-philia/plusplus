#!/bin/bash
# kill.sh - 按 PID 批量 kill 进程
# 用法: ./kill.sh 1234 5678 9012

if [ $# -eq 0 ]; then
    echo "用法: $0 <PID1> [PID2 ...]"
    exit 1
fi

for pid in "$@"; do
    # 校验是否为纯数字
    if ! [[ "$pid" =~ ^[0-9]+$ ]]; then
        echo ">>> 非法 PID: $pid （跳过）"
        continue
    fi

    # 检查进程是否存在
    if kill -0 "$pid" 2>/dev/null; then
        if kill -TERM "$pid" 2>/dev/null; then
            echo ">>> 已发送 SIGTERM 到 PID $pid"
        else
            echo ">>> kill 失败 PID $pid（可能权限不足）"
        fi
    else
        echo ">>> PID $pid 不存在或已退出"
    fi
done