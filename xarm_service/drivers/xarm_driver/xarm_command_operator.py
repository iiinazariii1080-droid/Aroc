from xarm.wrapper import XArmAPI
import arduino_controller.arduino_led_controller as als
from drivers.xarm_scripts import get_robot_data, take, put, move_to_position, get_position, move_tool_position, move_to_pose
from core.configuration import xarm_manipulator_ip
import logging

logger = logging.getLogger(__name__)

def xarm_command_operator(data):
    """
    Execute xArm robot commands.
    
    Args:
        data: Dictionary containing:
            - command: Command type (measure, move_to_position, take_to_box, take, put, get_current_position)
            - Additional command-specific parameters
            
    Returns:
        Dict containing:
            - success: bool indicating operation success
            - result: Command result or None if failed
            - error: Error message if failed, None if successful
            - error_code: Error code if failed, 0 if successful
    """
    arm = None
    robot_main = None
    
    try:
        # Connect to xArm
        arm = XArmAPI(xarm_manipulator_ip, baud_checkset=False)
        if not arm.connected:
            return {
                "success": False,
                "result": None,
                "error": "Failed to connect to xArm",
                "error_code": 500
            }
            
        # Create appropriate RobotMain instance based on command
        # if data["command"] == "measure":
        #     robot_main = measure.RobotMain(arm)
        if data["command"] == "move_to_position":
            robot_main = move_to_position.RobotMain(arm)
        elif data["command"] == "move_tool_position":
            robot_main = move_tool_position.RobotMain(arm)
        elif data["command"] == "move_to_pose":
            robot_main = move_to_pose.RobotMain(arm)
        elif data["command"] == "take":
            robot_main = take.RobotMain(arm)
        elif data["command"] == "put":
            robot_main = put.RobotMain(arm)
        elif data["command"] == "get_current_position":
            robot_main = get_position.RobotMain(arm)
        elif data["command"] == "get_data":
            robot_main = get_robot_data.RobotMain(arm)
        else:
            return {
                "success": False,
                "result": None,
                "error": f"Command {data['command']} not found",
                "error_code": 400
            }
            
        # Execute command
        result = robot_main.run(data)
        
        return {
            "success": True,
            "result": result,
            "error": None,
            "error_code": 0
        }
        
    except Exception as e:
        logger.error(f"XARM Operator error: {type(e).__name__}: {e}")
        return {
            "success": False,
            "result": None,
            "error": str(e),
            "error_code": 500
        }
    finally:
        if arm is not None:
            arm.disconnect()

# data= {}
# data["command"] = "move_to_pose"
# data["pose_name"] = "READY_SECTION_CENTER"

# result = xarm_command_operator(data)
# #