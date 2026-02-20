from setuptools import setup

package_name = 'vora_robot_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='VORA',
    maintainer_email='user@example.com',
    description='MyAGV-side command bridge for VORA (ROS2 Galactic).',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'command_executor = vora_robot_bridge.command_executor:main',
            'camera_bridge = vora_robot_bridge.camera_bridge:main',
        ],
    },
)
