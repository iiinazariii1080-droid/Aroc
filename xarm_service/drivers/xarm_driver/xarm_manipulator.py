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
import logging
from drivers.xarm_driver.xarm_positions import poses

logger = logging.getLogger(__name__)
from drivers.xarm_driver.picobot_lib import GripperController
from pydantic import BaseModel, Field
from drivers.xarm_driver import xarm_positions
from fastapi.concurrency import run_in_threadpool
import time
from app.types import (
    DepthQueryRequest,
    XarmMoveWithJointsParams,
    MoveWithToolParams as XarmMoveWithToolParams,
)
import asyncio

from app.exceptions import RobotError,InputError,DeviceError,Conflict
class XarmJointsDict(BaseModel):
    j1: float
    j2: float
    j3: float
    j4: float
    j5: float
    j6: float
def calibrate_distance(raw_distance: float) -> float:
    """
    Корректирует измеренное расстояние на основе эмпирической линейной модели.
    Формула получена по результатам калибровки:
    Истинное расстояние ≈ 0.937 * измеренное + 45.2
    """
    a = 0.957
    b = 45.2
    return a * raw_distance + b

def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Нет запущенного цикла — можно безопасно использовать run()
        return asyncio.run(coro)
    else:
        # Цикл уже запущен — запускаем таску
        return asyncio.create_task(coro)
    
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
        self.gripper_active = False
        self._last_alive = time.time()
        self._handle_joystick_stream_in_active = threading.Lock()
        try:
            self.gripper = GripperController(robot, baudrate=115200, timeout=10)
        except:
            print("Gripper is not available")

    def _robot_init(self):
        # Register callbacks only. No auto-recovery (explicit recover/enable_motion per SECURITY_SAFETY / ARCHITECTURE_V2).
        try:
            if not getattr(self._arm, "connected", False):
                return
            self._arm.register_error_warn_changed_callback(self._error_warn_changed_callback)
            self._arm.register_state_changed_callback(self._state_changed_callback)
            if hasattr(self._arm, "register_count_changed_callback"):
                self._arm.register_count_changed_callback(self._count_changed_callback)
        except Exception:
            pass

    def _error_warn_changed_callback(self, data):
        if data and data['error_code'] != 0:
            self.alive = False
            logger.warning('err=%s, quit', data['error_code'])
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)

    def _state_changed_callback(self, data):
        if data and data['state'] == 4:
            self.alive = False
            logger.warning('state=4, quit')
            self._arm.release_state_changed_callback(self._state_changed_callback)

    def _count_changed_callback(self, data):
        if self.is_alive:
            logger.debug('counter val: %s', data['count'])

    def _check_code(self, code, label):
        if not self.is_alive or code != 0:
            self.alive = False
            ret1 = self._arm.get_state()
            ret2 = self._arm.get_err_warn_code()
            logger.warning(
                '%s, code=%s, connected=%s, state=%s, error=%s, ret1=%s, ret2=%s',
                label, code, self._arm.connected, self._arm.state, self._arm.error_code, ret1, ret2
            )
        return self.is_alive

    @staticmethod
    def pprint(*args, **kwargs):
        """Legacy: log message (use logger in new code)."""
        try:
            stack_tuple = traceback.extract_stack(limit=2)[0]
            msg = '[{}][{}] {}'.format(
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())),
                stack_tuple[1],
                ' '.join(map(str, args))
            )
            logger.info("%s", msg)
        except Exception:
            logger.info(" %s", ' '.join(map(str, args)))

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

    async def complex_move_with_joints(self, data):
            return await run_in_threadpool(self._complex_move_with_joints, data)
    def _complex_move_with_joints(self,data):
        try:
            if not data.points or len(data.points) == 0:
                raise InputError(f"Position is not found in dictionary")
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            self._angle_speed = int(data.velocity_percent)
            self._angle_acc = int(data.velocity_percent)
            if not self.is_alive:
                raise RuntimeError("manipulator is not alive")
            for joints in data.points:
                code = self._arm.set_servo_angle(angle=[joints.j1, joints.j2, joints.j3, joints.j4, joints.j5, joints.j6], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=-1.0)
            if not self._check_code(code, 'set_position'):
                raise Conflict(f"set_servo_angle, code:{code}")
            return True
        finally:
            # self.alive = False
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
            self._arm.release_state_changed_callback(self._state_changed_callback)
            if hasattr(self._arm, 'release_count_changed_callback'):
                self._arm.release_count_changed_callback(self._count_changed_callback)
    
    async def move_with_joints(self, data):
            return await run_in_threadpool(self._move_with_joints, data)   
    def _move_with_joints(self,data):
        _error = None
        try:
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            self._angle_speed = int(data.velocity_percent)
            self._angle_acc = int(data.velocity_percent)
            if not self.is_alive:
                raise RobotError("manipulator is not alive")
            code = self._arm.set_servo_angle(angle=[data.j1, data.j2, data.j3, data.j4, data.j5, data.j6], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=-1.0)
            if not self._check_code(code, 'set_position'):
                raise Conflict(f"set_servo_angle, code:{code}")
            return True
        finally:
            # self.alive = False
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
            self._arm.release_state_changed_callback(self._state_changed_callback)
            if hasattr(self._arm, 'release_count_changed_callback'):
                self._arm.release_count_changed_callback(self._count_changed_callback)

    async def move_to_pose(self, data):
            return await run_in_threadpool(self._move_to_pose, data)
    def _move_to_pose(self,pose):
        try:
            if pose.name is None:
                raise RobotError("pose name is None")
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            self._angle_speed = int(pose.velocity_percent)
            self._angle_acc = int(pose.velocity_percent)
            if not self.is_alive:
                raise RobotError("manipulator is not alive")
            position = next((p for p in poses if p["name"] == pose.name), None)
            if position:
                position = position['joints']
            else:
                raise InputError(f"Position is not found in dictionary")
            code = self._arm.set_servo_angle(angle=[position["j1"], position["j2"], position["j3"], position["j4"], position["j5"], position["j6"]], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=-1.0)
            if not self._check_code(code, 'set_position'):
                raise Conflict(f"set_servo_angle, code:{code}")
            self._arm.set_world_offset([0, 0, 0, 0, 180, 120], is_radian=False, wait=True)
            # 2. Задаём границы куба в координатах полки
            shelf_boundary = [600, 0, 500, -500, 500, -500]  # пример: длина, ширина, высота полки
            self._arm.set_reduced_tcp_boundary(shelf_boundary)
            self._arm.set_reduced_mode(True)
            return True
        finally:
            # self.alive = False
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
            self._arm.release_state_changed_callback(self._state_changed_callback)
            if hasattr(self._arm, 'release_count_changed_callback'):
                self._arm.release_count_changed_callback(self._count_changed_callback)

    async def move_tool_position(self, data):
            return await run_in_threadpool(self._move_tool_position, data)
    def _move_tool_position(self,data=None):
        _error = None
        try:
            self._arm.set_mode(0)
            self._arm.set_state(0)
            time.sleep(0.2)
            if not self.is_alive:
                raise RobotError("manipulator is not alive")
            self._angle_speed = int(data.velocity_percent)
            self._angle_acc = int(data.velocity_percent)
            code = self._arm.set_tool_position(z=int(data.z_offset_mm),y=int(data.y_offset_mm),x=int(data.x_offset_mm), radius=0, speed=self._angle_speed, mvacc=self._angle_acc, relative=True, wait=True)
            if not self._check_code(code, 'set_position'):
                raise Conflict(f"set_servo_angle, code:{code}")
            return True
        finally:
            # self.alive = False
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
            self._arm.release_state_changed_callback(self._state_changed_callback)
            if hasattr(self._arm, 'release_count_changed_callback'):
                self._arm.release_count_changed_callback(self._count_changed_callback)

    def drop(self):
        # return True
        _error = None
        try:
            code = self.gripper.deactivate()
            time.sleep(1)
            if code == [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]:
                self.gripper_active = False
                return True
            else:
                raise RobotError("drop failed")
        except Exception as e:
            # from core.logger import server_logger
            # server_logger.log_event("error", f"suction_error: {e}")
            _error = e

        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RobotError(f"suction_error: {_error}")
    
    def take(self):
        # return True
        _error = None
        try:
            code = self.gripper.activate()
            time.sleep(1)
            if code == [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]:
                self.gripper_active = True
                return True
            else:
                raise RobotError("take failed")
        except Exception as e:
            # from core.logger import server_logger
            # server_logger.log_event("error", f"suction_error: {e}")
            _error = e

        try:
            code = self.gripper.deactivate()
            if code == [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]:
                self.gripper_active = False
                return True
        except Exception as e:
            # from core.logger import server_logger
            # server_logger.log_event("error", f"suction_error: {e}")
            _error = e
        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
        raise RobotError(f"suction_error: {_error}")
    
    def get_status(self,data=None):
        try:
            connected = bool(getattr(self._arm, 'connected', False))
            state_code = getattr(self._arm, 'state', 0) or 0
            has_err_warn_val = getattr(self._arm, 'has_err_warn', False)
            has_error_val = getattr(self._arm, 'has_error', False)
            has_warn_val = getattr(self._arm, 'has_warn', False)
            error_code = getattr(self._arm, 'error_code', 0) or 0
            return {
                "alive": bool(self.alive and connected),
                "connected": connected,
                "state_code": int(state_code),
                "has_err_warn": bool(has_err_warn_val) if has_err_warn_val is not None else False,
                "has_error": bool(has_error_val) if has_error_val is not None else False,
                "has_warn": bool(has_warn_val) if has_warn_val is not None else False,
                "error_code": int(error_code),
            }
        except Exception as e:
            logger.exception('MainException: %s', e)
        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)

        raise RobotError("get_robot_data failed")
    
    def get_nearest_joints_position(self,data=None):
        try:
            resp = self.arm.get_servo_angle()
            code = 0
            angles = None
            if isinstance(resp, tuple) and len(resp) >= 2:
                code, angles = resp[0], resp[1]
            else:
                angles = resp
            if code != 0 or not isinstance(angles, (list, tuple)) or len(angles) < 6:
                raise RobotError("failed to read joints position")
            norm = []
            for i in range(6):
                val = angles[i]
                try:
                    norm.append(float(val))
                except Exception:
                    raise RobotError("invalid joints data")
            current_position = {
                "name": "CURRENT",
                "joints": {
                    "j1": round(norm[0], 3),
                    "j2": round(norm[1], 3),
                    "j3": round(norm[2], 3),
                    "j4": round(norm[3], 3),
                    "j5": round(norm[4], 3),
                    "j6": round(norm[5], 3),
                }
            }
            result = xarm_positions.find_closest_position(current_position)
            # Ensure floats for StrictFloat compatibility
            result["joints"] = {k: float(v) for k, v in result.get("joints", {}).items()}
            return result
        except Exception as e:
            logger.exception('MainException: %s', e)
            if poses:
                return poses[0]
        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)

        raise RobotError("get_position failed")
    
    def get_joints_position(self,data=None):
        try:
            resp = self.arm.get_servo_angle()
            code = 0
            angles = None
            if isinstance(resp, tuple) and len(resp) >= 2:
                code, angles = resp[0], resp[1]
            else:
                angles = resp
            if code != 0 or not isinstance(angles, (list, tuple)) or len(angles) < 6:
                raise RobotError("failed to read joints position")
            norm = []
            for i in range(6):
                val = angles[i]
                try:
                    norm.append(float(val))
                except Exception:
                    raise RobotError("invalid joints data")
            return {
                "name": "CURRENT",
                "joints": {
                    "j1": round(norm[0], 3),
                    "j2": round(norm[1], 3),
                    "j3": round(norm[2], 3),
                    "j4": round(norm[3], 3),
                    "j5": round(norm[4], 3),
                    "j6": round(norm[5], 3),
                }
            }
        except Exception as e:
            logger.exception('MainException: %s', e)
            if poses:
                return poses[0]
        # self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)

        raise RobotError("get_position failed")
    
    async def handle_joystick_stream(self, data):
            return await run_in_threadpool(self._handle_joystick_stream, data)

    async def autotake(self, data) -> bool:
        from services.depth_service import DepthService
        from db.trajectory import get_trajectory, save_trajectory
        forward_distance = await DepthService.get_depth(x=data.x, y=data.y)
        distance = calibrate_distance(forward_distance)
        config = get_trajectory();
        current_position = self.get_joints_position()
        current_position = current_position['joints']
        if config['prefix']['active']:
            prefix_position = XarmMoveWithToolParams(x_offset_mm=config['prefix']['posX'],
                                                    y_offset_mm=config['prefix']['posY'],
                                                    z_offset_mm=config['prefix']['posZ'],
                                                    velocity_percent=config['prefix']['speed'],
                                                    reset_faults=False,
                                                    blocking=True)
            await self.move_tool_position(prefix_position)

        if config['gripper']['active']: 
            self.take()

        if config['baseMove']['active']:
            take_position = XarmMoveWithToolParams(x_offset_mm=config['baseMove']['posX'],
                                                    y_offset_mm=config['baseMove']['posY'],
                                                    z_offset_mm=config['baseMove']['posZ']+distance,
                                                    velocity_percent=config['baseMove']['speed'],
                                                    reset_faults=False,
                                                    blocking=True)
            await self.move_tool_position(take_position)

        if config['postfix']['active']:
            prefix_position = XarmMoveWithToolParams(x_offset_mm=config['postfix']['posX'],
                                                    y_offset_mm=config['postfix']['posY'],
                                                    z_offset_mm=config['postfix']['posZ'],
                                                    velocity_percent=config['baseMove']['speed'],
                                                    reset_faults=False,
                                                    blocking=True)
            await self.move_tool_position(take_position)

        if config['return']['active']: 
            velocity_percent = config['baseMove']['speed']
            current_position['velocity_percent']=velocity_percent
            current_position['reset_faults']=False
            current_position['blocking']=True
            return_position = XarmMoveWithJointsParams(**current_position)
            await  self.move_with_joints(return_position)

    def _handle_joystick_stream(self, joystick_id: str):
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

                if buttons.get("button3", False):
                    if self.gripper_active:
                        self.drop()
                    else:
                        data = DepthQueryRequest(x = 320, y = 240)
                        async def wrapper():
                            await self.autotake(data)
                        try:
                            loop = asyncio.get_running_loop()
                            asyncio.create_task(wrapper())  # fire-and-forget
                        except RuntimeError:
                            asyncio.run(wrapper())  # если нет активного event loop

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
        """DEPRECATED: Auto-recovery is intentionally disabled.

        Use the explicit API command `POST /recover` (CommandType.RECOVER_FAULTS) instead.
        This method is kept only for backward compatibility with legacy code paths.
        """
        return False


