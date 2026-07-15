#!/usr/bin/env python3
# Custom navigation launch for VORA MyAGV.
#
# Identical to /opt/ros/galactic/share/nav2_bringup/launch/navigation_launch.py
# with one addition: lifecycle_manager_navigation also receives configured_params
# so that bond_timeout: 30.0 (set in nav2_params.yaml) is actually applied.
#
# Why this is needed:
#   The stock launch file passes only inline dicts to lifecycle_manager_navigation,
#   so nav2_params.yaml values for that node (bond_timeout, etc.) are silently ignored.
#   On Jetson Nano, DDS bond heartbeat establishment takes > default 4s → the manager
#   fires its bond timer, sees no heartbeat, and re-initiates managed-node bringup
#   ("Starting managed nodes bringup...") on an already-active stack → "transition 1
#   is invalid because node is already active" → controller_server fails to configure
#   a second time ("Failed to change state for node: controller_server").

import os

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    bringup_dir = get_package_share_directory('nav2_bringup')

    namespace = LaunchConfiguration('namespace')
    use_sim_time = LaunchConfiguration('use_sim_time')
    autostart = LaunchConfiguration('autostart')
    params_file = LaunchConfiguration('params_file')

    lifecycle_nodes = ['controller_server',
                       'planner_server',
                       'recoveries_server',
                       'bt_navigator',
                       'waypoint_follower']

    remappings = [('/tf', 'tf'),
                  ('/tf_static', 'tf_static')]

    param_substitutions = {
        'use_sim_time': use_sim_time,
        'autostart': autostart}

    configured_params = RewrittenYaml(
        source_file=params_file,
        root_key=namespace,
        param_rewrites=param_substitutions,
        convert_types=True)

    return LaunchDescription([
        SetEnvironmentVariable('RCUTILS_LOGGING_BUFFERED_STREAM', '1'),

        DeclareLaunchArgument(
            'namespace', default_value='',
            description='Top-level namespace'),

        DeclareLaunchArgument(
            'use_sim_time', default_value='false',
            description='Use simulation (Gazebo) clock if true'),

        DeclareLaunchArgument(
            'autostart', default_value='true',
            description='Automatically startup the nav2 stack'),

        DeclareLaunchArgument(
            'params_file',
            default_value=os.path.join(bringup_dir, 'params', 'nav2_params.yaml'),
            description='Full path to the ROS2 parameters file to use'),

        Node(
            package='nav2_controller',
            executable='controller_server',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),

        Node(
            package='nav2_planner',
            executable='planner_server',
            name='planner_server',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),

        Node(
            package='nav2_recoveries',
            executable='recoveries_server',
            name='recoveries_server',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),

        Node(
            package='nav2_bt_navigator',
            executable='bt_navigator',
            name='bt_navigator',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),

        Node(
            package='nav2_waypoint_follower',
            executable='waypoint_follower',
            name='waypoint_follower',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),

        # KEY FIX: also pass configured_params to lifecycle_manager_navigation
        # so bond_timeout: 30.0 from nav2_params.yaml is applied.
        # Without this, the manager uses bond_timeout=4.0 (default) which expires
        # before DDS bond heartbeat is established on Jetson Nano → second bringup
        # on already-active nodes → "Failed to change state for node: controller_server".
        # Inline overrides (autostart, node_names) come after and take precedence
        # for those specific keys while letting the yaml supply bond_timeout.
        Node(
            package='nav2_lifecycle_manager',
            executable='lifecycle_manager',
            name='lifecycle_manager_navigation',
            output='screen',
            parameters=[configured_params,
                        {'use_sim_time': use_sim_time},
                        {'autostart': autostart},
                        {'node_names': lifecycle_nodes}]),
    ])


