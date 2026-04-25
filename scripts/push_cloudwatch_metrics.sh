#!/usr/bin/env bash
# Push custom CloudWatch metrics for openEar monitoring.
#
# Run via cron every 5 minutes:
#   */5 * * * * /path/to/push_cloudwatch_metrics.sh
#
# Pushes:
#   - DiskUsagePercent: percentage of disk used on root filesystem
#   - MemoryUsagePercent: percentage of memory used
#   - ContainerRunning: 1 if openear container is running, 0 if not

set -euo pipefail

NAMESPACE="openEar"
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || echo "local")
REGION="${AWS_REGION:-us-west-2}"

# Disk usage (root filesystem)
DISK_USED_PCT=$(df / | awk 'NR==2 {print $5}' | tr -d '%')

# Memory usage
MEM_TOTAL=$(free -m | awk '/^Mem:/ {print $2}')
MEM_USED=$(free -m | awk '/^Mem:/ {print $3}')
MEM_PCT=$(( MEM_USED * 100 / MEM_TOTAL ))

# Container liveness
CONTAINER_RUNNING=0
if docker inspect --format='{{.State.Running}}' openear 2>/dev/null | grep -q "true"; then
    CONTAINER_RUNNING=1
fi

# Push metrics
aws cloudwatch put-metric-data \
    --region "${REGION}" \
    --namespace "${NAMESPACE}" \
    --metric-data \
        "MetricName=DiskUsagePercent,Value=${DISK_USED_PCT},Unit=Percent,Dimensions=[{Name=InstanceId,Value=${INSTANCE_ID}}]" \
        "MetricName=MemoryUsagePercent,Value=${MEM_PCT},Unit=Percent,Dimensions=[{Name=InstanceId,Value=${INSTANCE_ID}}]" \
        "MetricName=ContainerRunning,Value=${CONTAINER_RUNNING},Unit=None,Dimensions=[{Name=InstanceId,Value=${INSTANCE_ID}}]"

echo "$(date '+%Y-%m-%d %H:%M:%S') Pushed metrics: disk=${DISK_USED_PCT}% mem=${MEM_PCT}% container=${CONTAINER_RUNNING}"
