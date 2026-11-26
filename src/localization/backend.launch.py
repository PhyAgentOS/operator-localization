"""Default managed localization backend; robot topics and parameters are supplied by configuration."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    params = LaunchConfiguration("params_file")
    definitions = [('map_server','nav2_map_server','map_server',[]),('amcl','nav2_amcl','amcl',[])]
    names = ['map_server', 'amcl']
    actions = []
    for name, package, executable, remappings in definitions:
        node = Node(name=name, package=package, executable=executable, output="screen",
                    parameters=[params], remappings=remappings)
        actions += [node, RegisterEventHandler(OnProcessExit(target_action=node,
                    on_exit=[EmitEvent(event=Shutdown(reason=name+" exited"))]))]
    actions.append(Node(package="nav2_lifecycle_manager", executable="lifecycle_manager",
        name="lifecycle_manager_localization", output="screen", parameters=[params, {
            "autostart": ParameterValue(LaunchConfiguration("autostart"),value_type=bool),
            "node_names": names, "bond_timeout": 4.0}]))
    return LaunchDescription([DeclareLaunchArgument("params_file"),
        DeclareLaunchArgument("mode", default_value="localization"),
        DeclareLaunchArgument("autostart", default_value="false"),
        DeclareLaunchArgument("command_topic", default_value="/cmd_vel_nav"),
        DeclareLaunchArgument("smoothed_topic", default_value="/cmd_vel_smoothed"), *actions])
