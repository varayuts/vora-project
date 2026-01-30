#!/usr/bin/env bash
set -e
source /opt/ros/galactic/setup.bash
source ~/myagv_ros2/install/setup.bash

OUTDIR=~/Desktop/VORA_myAGV_only_ros2_package/maps
mkdir -p "$OUTDIR"

NAME=${1:-myagv_map_$(date +%Y%m%d_%H%M%S)}
echo "Saving map to: $OUTDIR/$NAME.(pgm|yaml)"

# nav2 map saver
ros2 run nav2_map_server map_saver_cli -f "$OUTDIR/$NAME"
echo "Done."
ls -lh "$OUTDIR" | tail -n 5
