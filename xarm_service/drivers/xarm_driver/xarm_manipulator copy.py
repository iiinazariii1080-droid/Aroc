#!/usr/bin/env python3
# Software License Agreement (BSD License)
#
# Copyright (c) 2022, UFACTORY, Inc.
# All rights reserved.
#
# Author: Vinman <vinman.wen@ufactory.cc> <vinman.cub@gmail.com>

"""
# Notice
#   1. Changes to this file on Studio will not be preserved
#   2. The next conversion will overwrite the file with the same name
# 
# xArm-Python-SDK: https://github.com/xArm-Developer/xArm-Python-SDK
#   1. git clone git@github.com:xArm-Developer/xArm-Python-SDK.git
#   2. cd xArm-Python-SDK
#   3. python setup.py install
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/../.."))
import threading
import time
import traceback
from xarm import version
from xarm.wrapper import XArmAPI
import sys
from drivers.xarm_driver.xarm_positions import poses
from drivers.xarm_driver.picobot_lib import GripperController
from pydantic import BaseModel, Field
from drivers.xarm_driver import xarm_positions

class XarmJointsDict(BaseModel):
    j1: float
    j2: float
    j3: float
    j4: float
    j5: float
    j6: float

class RobotMain(object):
    """Robot Main Class"""
    def __init__(self, robot, **kwargs):
        self.alive = True
        self._arm = robot
        self._tcp_speed = 100
        self._tcp_acc = 2000
        self._angle_speed = 20
        self._angle_acc = 500
        self._vars = {}
        self._funcs = {}
        self._robot_init()
        self._last_command = None
        self._last_time = 0
        self._failures = 0
        self._last_alive = time.time()
        self._handle_joystick_stream_in_active = threading.Lock()
        try:
            self.gripper = GripperController(robot, baudrate=115200, timeout=100)
        except:
            print("Gripper is not available")

    def _robot_init(self):
        self._arm.clean_warn()
        self._arm.clean_error()
        self._arm.motion_enable(True)
        self._arm.set_mode(0)
        self._arm.set_state(0)
        time.sleep(0.5)
        self._arm.register_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.register_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'register_count_changed_callback'):
            self._arm.register_count_changed_callback(self._count_changed_callback)

    def _error_warn_changed_callback(self, data):
        if data and data['error_code'] != 0:
            self.alive = False
            self.pprint('err={}, quit'.format(data['error_code']))
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)

    def _state_changed_callback(self, data):
        if data and data['state'] == 4:
            self.alive = False
            self.pprint('state=4, quit')
            self._arm.release_state_changed_callback(self._state_changed_callback)

    def _count_changed_callback(self, data):
        if self.is_alive:
            self.pprint('counter val: {}'.format(data['count']))

    def _check_code(self, code, label):
        if not self.is_alive or code != 0:
            self.alive = False
            ret1 = self._arm.get_state()
            ret2 = self._arm.get_err_warn_code()
            self.pprint('{}, code={}, connected={}, state={}, error={}, ret1={}. ret2={}'.format(label, code, self._arm.connected, self._arm.state, self._arm.error_code, ret1, ret2))
        return self.is_alive

    @staticmethod
    def pprint(*args, **kwargs):
        try:
            stack_tuple = traceback.extract_stack(limit=2)[0]
            msg = '[{}][{}] {}'.format(
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())),
                stack_tuple[1],
                ' '.join(map(str, args))
            )
        except Exception:
            msg = ' '.join(map(str, args))

    @property
    def arm(self):
        return self._arm

    @property
    def VARS(self):
        return self._vars

    @property
    def FUNCS(self):
        return self._funcs

    @property
    def is_alive(self):
        try:
            if not self._arm.connected:
                return False
            if self._arm.error_code != 0:
                return False
            # state==1, 2, or 6 - ok, всё остальное - нет
            # State 6 appears to be another ready state based on logs
            is_ready = self._arm.state in [1, 2, 6]
            return is_ready
        except Exception as e:
            return False


    def complex_move_with_joints(self,data):
        _error = None
        try:
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            self._angle_speed = int(data.velocity_percent)
            self._angle_acc = int(data.velocity_percent)
            if not self.is_alive:
                raise RuntimeError("manipulator is not alive")
            for joints in data.points:
                code = self._arm.set_servo_angle(angle=[joints.j1_deg, joints.j2_deg, joints.j3_deg, joints.j4_deg, joints.j5_deg, joints.j6_deg], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=-1.0)
                if not self._check_code(code, 'set_position'):
                    raise RuntimeError(f"set_servo_angle, code:{code}")
            return True
        except Exception as e:
            _error = e

        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RuntimeError(f"move_to_pose failed: {_error}")
    
    def move_with_joints(self,data):
        _error = None
        try:
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            self._angle_speed = int(data.velocity_percent)
            self._angle_acc = int(data.velocity_percent)
            if not self.is_alive:
                raise RuntimeError("manipulator is not alive")
            code = self._arm.set_servo_angle(angle=[data.j1_deg, data.j2_deg, data.j3_deg, data.j4_deg, data.j5_deg, data.j6_deg], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=-1.0)
            if not self._check_code(code, 'set_position'):
                raise RuntimeError(f"set_servo_angle, code:{code}")
            return True
        except Exception as e:
            _error = e

        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RuntimeError(f"move_to_pose failed: {_error}")
    
    def move_to_pose(self,data):
        _error = None
        try:
            if data.pose_name is None:
                raise RuntimeError("pose name is None")
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            self._angle_speed = int(data.velocity_percent)
            self._angle_acc = int(data.velocity_percent)
            if not self.is_alive:
                raise RuntimeError("manipulator is not alive")
            position = poses[data.pose_name]
            code = self._arm.set_servo_angle(angle=[position["j1"], position["j2"], position["j3"], position["j4"], position["j5"], position["j6"]], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=-1.0)
            if not self._check_code(code, 'set_position'):
                raise RuntimeError(f"set_servo_angle, code:{code}")
            return True
        except Exception as e:
            _error = e

        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RuntimeError(f"move_to_pose failed: {_error}")
    
    def move_tool_position(self,data=None):
        _error = None
        try:
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            if not self.is_alive:
                raise RuntimeError("manipulator is not alive")
            self._angle_speed = int(data.velocity_percent)
            self._angle_acc = int(data.velocity_percent)
            code = self._arm.set_tool_position(z=int(data.z_offset_mm),y=int(data.y_offset_mm),x=int(data.x_offset_mm), radius=0, speed=self._tcp_speed, mvacc=self._tcp_acc, relative=True, wait=True)
            if not self._check_code(code, 'set_position'):
                raise RuntimeError(f"set_tool_position, code:{code}")
            return True
        except Exception as e:
            _error = e

        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RuntimeError(f"move_tool_position failed: {_error}")

    def drop(self):
        # return True
        _error = None
        try:
            code = self.gripper.deactivate()
            if code == [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]:
                return True
            else:
                raise RuntimeError("drop failed")
        except Exception as e:
            from core.logger import server_logger
            server_logger.log_event("error", f"suction_error: {e}")
            _error = e

        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RuntimeError(f"suction_error: {_error}")
    
    def take(self):
        # return True
        _error = None
        try:
            code = self.gripper.activate()
            if code == [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]:
                return True
            else:
                raise RuntimeError("take failed")
        except Exception as e:
            from core.logger import server_logger
            server_logger.log_event("error", f"suction_error: {e}")
            _error = e

        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RuntimeError(f"suction_error: {_error}")
    
    def get_status(self,data=None):
        try:
            return {
                "alive": self.alive,
                "connected": self._arm.connected,
                "state_code": self._arm.state,
                "has_err_warn": self._arm.has_err_warn,
                "has_error": self._arm.has_error,
                "has_warn": self._arm.has_warn,
                "error_code": self._arm.error_code,
            }
        except Exception as e:
            self.pprint('MainException: {}'.format(e))
        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)

        raise RuntimeError("get_robot_data failed")
    
    async def get_current_position(self,data=None):
        try:
            joints = self.arm.get_servo_angle()[1]
            current_position = []
            current_position.append("CURRENT")
            current_position.append({
            "j1":round(joints[0]),
            "j2":round(joints[1]),
            "j3":round(joints[2]),
            "j4":round(joints[3]),
            "j5":round(joints[4]),
            "j6":round(joints[5])
            })
            return xarm_positions.find_closest_position(current_position)
        except Exception as e:
            self.pprint('MainException: {}'.format(e))
        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)

        raise RuntimeError("get_position failed")
    
    def get_joints_position(self,data=None):
        try:
            joints = self.arm.get_servo_angle()[1]
            current_position = []
            current_position.append("CURRENT")
            current_position.append({
            "j1":round(joints[0]),
            "j2":round(joints[1]),
            "j3":round(joints[2]),
            "j4":round(joints[3]),
            "j5":round(joints[4]),
            "j6":round(joints[5])
            })
            return current_position
        except Exception as e:
            self.pprint('MainException: {}'.format(e))
        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)

        raise RuntimeError("get_position failed")




    def handle_joystick_stream(self, joystick_id: str):
        import time
        from core.state import virtual_joysticks
        if not self._handle_joystick_stream_in_active.acquire(blocking=False):
            raise RuntimeError("Joystick stream handler already running")
        try:
            _error = None
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            if not self.is_alive:
                raise RuntimeError("manipulator is not alive")
            
            last_activity_time = time.time()
            TICK = 0.05  # 20 Hz
            MAX_SPEED = 100  # mm/s
            ACCEL_TIME = 1.5  # seconds
            DECEL_TIME = 0.5  # seconds
            MVACC = MAX_SPEED / ACCEL_TIME  # mm/s^2
            MVDEC = MAX_SPEED / DECEL_TIME  # mm/s^2
            DEADZONE = 0.05
            INACTIVITY_TIMEOUT = 0.2
            current_speed = 0

            while True:
                # Check if joystick still exists
                if joystick_id not in virtual_joysticks:
                    break

                joystick_data = virtual_joysticks[joystick_id]
                axes = joystick_data.get("axes", {})
                buttons = joystick_data.get("buttons", {})
                has_activity = virtual_joysticks[joystick_id]['activities']['manipulator']
                # Update activity time
                if not has_activity:
                    break
                last_activity = joystick_data.get("last_activity", 0)
                if time.time() - last_activity > INACTIVITY_TIMEOUT:
                    # слишком старый input, ничего не делаем, break/return
                    break
                if current_speed < MAX_SPEED:
                    current_speed += MVACC * TICK
                    if current_speed > MAX_SPEED:
                        current_speed = MAX_SPEED
                else:
                    if current_speed > 0:
                        current_speed -= MVDEC * TICK
                        if current_speed < 0: current_speed = 0
                offset = current_speed * TICK

                def apply_deadzone(value, dz=DEADZONE):
                    return value if abs(value) > dz else 0.0
                
                # Вычисляем оффсеты движения
                x_offset = 0.0
                y_offset = 0.0
                z_offset = 0.0
                _yaw = 0
                _roll = 0
                _pitch = 0

                movement_map = {
                    "button11": ("z", 1),
                    "button9":  ("z", -1),
                    "button15": ("x", -1),
                    "button13": ("x", 1),
                    "button12": ("y", 1),
                    "button14": ("y", -1)
                }
                for btn, (coord, sign) in movement_map.items():
                    if buttons.get(btn, False):
                        if coord == "x": x_offset += sign * offset
                        if coord == "y": y_offset += sign * offset
                        if coord == "z": z_offset += sign * offset

                if buttons.get("button2", False):  # Кнопка Y/Triangle - движение назад по X
                    _yaw = -apply_deadzone(axes["axis2"])
                else:
                    _roll = -apply_deadzone(axes["axis2"])
                    _pitch = -apply_deadzone(axes["axis5"])
                
                # Only move if there's actual input
                if x_offset != 0.0 or y_offset != 0.0 or z_offset != 0.0 or _roll!= 0.0 or _yaw!= 0.0 or _pitch!= 0.0:
                    code = self._arm.set_tool_position(
                        x=int(y_offset), y=int(x_offset), z=int(z_offset),
                        radius=0,  roll=int(_roll), pitch=int(_pitch),yaw=int(_yaw), speed=MAX_SPEED, mvacc=MVACC,
                        relative=True, wait=False
                    )
                    
                    if not self._check_code(code, 'set_tool_position'):
                        raise RuntimeError(f"set_tool_position, code:{code}")
                    
                    time.sleep(TICK)
                        
            self._arm.set_state(4)
        except Exception as e:
            _error = e
        finally:
            self._handle_joystick_stream_in_active.release()
        # Cleanup callbacks on error
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        
        if _error:
            raise RuntimeError(f"handle_joystick_stream failed: {_error}")


    def unlock_safe_mode(self):
        result = False
        try:
            if self._arm.state in (3, 4, 5):
                print("Trying auto-recover ...")
                self._arm.clean_error()
                self._arm.motion_enable(True)
                self._arm.set_mode(0)
                self._arm.set_state(0)
                time.sleep(1)
                if self._arm.state == 2:
                    result = True
        finally:
            return result

