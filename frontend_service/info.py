openapi_tags = [
    {
        "name": "Robot AE.01",
        "description": (
            "Composite endpoints for managing the complete robot AE.01 cell. "
            "This includes coordinated actions across the Igus lift, xArm manipulator, and Symovo AGV. "
            "Use these endpoints to trigger complex scenarios such as moving products, resetting faults, "
            "and monitoring the combined status of all subsystems.\n\n"
            "**Typical use-cases:**\n"
            "- Move product to a specified location\n"
            "- Send robot to transport (stowed) position\n"
            "- Drop product to box positions\n"
            "- Get full system status (Igus, xArm, AGV)"
        )
    },
    {
        "name": "xArm Manipulator",
        "description": (
            "Endpoints for the UFactory xArm 6 axis manipulator. "
            "Provide motion control, gripper operations and status monitoring."
        ),
    },
    {
        "name": "Igus Motor",
        "description": (
            "Endpoints for controlling the Igus lifting motor:\n"
            "- Position control\n"
            "- Speed and acceleration settings\n"
            "- Homing/reference\n"
            "- Fault reset\n"
            "- Status monitoring\n"
            "\n"
            "Note: Only one command can be processed at a time; 423 Locked will be returned if busy."
        ),
    },
    {
        "name": "Symovo AGV",
        "description": (
            "Endpoints for interacting with the Symovo autonomous guided vehicle (AGV). "
            "Support fetching maps, starting jobs and monitoring state."
        ),
    },
    {
        "name": "misc",
        "description": "Utility endpoints: serving static files, trajectories and helper functions.",
    },
    {
        "name": "ROS2 Bridge Installation Guide",
        "description": (
            "How to build and use the ROS2 Bridge and interface package:\n"
            "\n"
            "1. Download the bridge repository:\n"
            "   from https://github.com/AliaksandrNazaruk/aroc_public_data/tree/main/ros2_bridge\n"
            "\n"
            "2. Copy the ros2_bridge directory to your ROS2 workspace src/:\n"
            "   cp -r aroc.github.io/ros2_bridge ~/test_ws/src/\n"
            "\n"
            "3. (If using custom services) Make sure the interface package robot_bridge2_interfaces is also present in src/.\n"
            "\n"
            "4. Build the workspace:\n"
            "   cd ~/test_ws\n"
            "   colcon build --symlink-install\n"
            "\n"
            "5. Source the workspace:\n"
            "   source install/setup.bash\n"
            "\n"
            "6. Run the bridge node:\n"
            "   ros2 run robot_bridge2 api_bridge --ros-args -p base_url:=http://robot_ip:port\n"
            "\n"
            "7. Make the test call:\n"
            "   ros2 service call /call_api robot_bridge2_interfaces/srv/CallAPI {method: 'GET', endpoint: '/api/v1/igus/motor/status', json: ''}\n"
            "\n"
            "8. From any ROS2 client in the same network (with robot_bridge2_interfaces installed), you can call the bridge services.\n"
        ),
    },
]

description="""
The **AE.01 API** provides unified REST endpoints to manage an industrial robotics cell built from an Igus actuator, UFactory xArm manipulator, and Symovo AGV.

---

**Warning:**  
This API is a work in progress and subject to change.  
Features, endpoints, and response formats may evolve as development continues.  
Unexpected bugs are possible — use with caution and check for updates regularly.

---

### Quick start

1. Check system status: **`/api/v1/robot/status`**
2. Robot reconfig to transport position: **`/api/v1/robot/move/to_transport_position`**
3. Move robot to product: **`/api/v1/robot/move/to_product`**
4. **Manually pick up the product:**  
Open http://robot_ip:port/control web page and execute the grasp action manually.
5. Place product in box: **`/api/v1/robot/move/to_box_1`** or **`/api/v1/robot/move/to_box_2`**
6. Disable Gripper **`api/v1/xarm/manipulator/drop`**


---

- All endpoints are available on the local network, no authentication required.
- Composite endpoints coordinate multiple subsystems for you.
- Non-200 responses include error messages for debugging.
- See individual tags for device-specific endpoints.

"""