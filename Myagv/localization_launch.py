#!/usr/bin/env python3
# Custom localization launch for VORA MyAGV.
#
# Identical to /opt/ros/galactic/share/nav2_bringup/launch/localization_launch.py
# with one addition: lifecycle_manager_localization also receives configured_params
# so that bond_timeout: 30.0 (set in nav2_params.yaml) is actually applied.
#
# Why this is needed:
#   The stock launch file passes only inline dicts to lifecycle_manager_localization,
#   so nav2_params.yaml values for that node (bond_timeout, etc.) are silently ignored.
#   On Jetson Nano, DDS bond heartbeat establishment takes > default 4s → the manager
#   fires its bond timer, sees no heartbeat, and re-initiates managed-node bringup
#   ("Starting managed nodes bringup...") on an already-active stack → "transition 1
#   is invalid because node is already active" → localization stuck / oscillating.

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
    map_yaml_file = LaunchConfiguration('map')
    use_sim_time = LaunchConfiguration('use_sim_time')
    autostart = LaunchConfiguration('autostart')
    params_file = LaunchConfiguration('params_file')
    lifecycle_nodes = ['map_server', 'amcl']

    remappings = [('/tf', 'tf'),
                  ('/tf_static', 'tf_static')]

    param_substitutions = {
        'use_sim_time': use_sim_time,
        'yaml_filename': map_yaml_file}

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
            'map',
            default_value=os.path.join(bringup_dir, 'maps', 'turtlebot3_world.yaml'),
            description='Full path to map yaml file to load'),

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
            package='nav2_map_server',
            executable='map_server',
            name='map_server',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),

        Node(
            package='nav2_amcl',
            executable='amcl',
            name='amcl',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),

        # KEY FIX: also pass configured_params to lifecycle_manager_localization
        # so bond_timeout: 30.0 from nav2_params.yaml is applied.
        # Inline overrides (autostart, node_names) come after and take precedence
        # for those specific keys while letting the yaml supply bond_timeout.
        Node(
            package='nav2_lifecycle_manager',
            executable='lifecycle_manager',
            name='lifecycle_manager_localization',
            output='screen',
            parameters=[configured_params,
                        {'use_sim_time': use_sim_time},
                        {'autostart': autostart},
                        {'node_names': lifecycle_nodes}]),
    ])
