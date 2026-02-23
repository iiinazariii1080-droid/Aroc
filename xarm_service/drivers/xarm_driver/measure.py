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
from drivers.xarm_scripts import measure_xy
import sys
import time
from xarm.wrapper import XArmAPI
import subprocess
import ast
from drivers.xarm_scripts import xarm_positions
import os
from core.configuration import (
    measure_desk_manipulator_difference,
    tcp_speed,
    tcp_acceleration,
    angle_speed,
    angle_acceleration
)

class RobotMain(object):
    """Robot Main Class"""
    def __init__(self, robot, **kwargs):
        self.alive = True
        self._arm = robot
        self._tcp_speed = tcp_speed
        self._tcp_acc = tcp_acceleration
        self._angle_speed = angle_speed
        self._angle_acc = angle_acceleration
        self._vars = {}
        self._funcs = {}
        self._robot_init()

    # Robot init
    def _robot_init(self):
        self._arm.clean_warn()
        self._arm.clean_error()
        self._arm.motion_enable(True)
        self._arm.set_mode(0)
        self._arm.set_state(0)
        time.sleep(1)
        self._arm.register_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.register_state_changed_callback(self._state_changed_callback)

    # Register error/warn changed callback
    def _error_warn_changed_callback(self, data):
        if data and data['error_code'] != 0:
            self.alive = False
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)

    # Register state changed callback
    def _state_changed_callback(self, data):
        if data and data['state'] == 4:
            self.alive = False
            self._arm.release_state_changed_callback(self._state_changed_callback)

    def _check_code(self, code, label):
        if not self.is_alive or code != 0:
            self.alive = False
        return self.is_alive

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
        if self.alive and self._arm.connected and self._arm.error_code == 0:
            if self._arm.state == 5:
                cnt = 0
                while self._arm.state == 5 and cnt < 5:
                    cnt += 1
                    time.sleep(0.1)
            return self._arm.state < 4
        else:
            return False

    # Robot Main Run
    def run(self,data):
        correct_position = data["correct_position"]
        move_to_measure_desk = data["move_to_measure_desk"]
        # Joint Motion
        self._angle_speed = angle_speed
        self._angle_acc = angle_acceleration
        for i in range(int(1)):
            if not self.is_alive:
                break
            if move_to_measure_desk:
                code = self._arm.set_servo_angle(angle=xarm_positions.measure_desk, speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=0.0)
                if not self._check_code(code, 'set_servo_angle'):
                    return
            result = list(measure_xy.stream_depth_frames(correct_position))
            new_result = int(measure_desk_manipulator_difference)-int(result[4])
            result[4] = float(new_result)
        self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        return result


correct_position = False
response_data = []

# arm = XArmAPI('192.168.1.220', baud_checkset=False)
# robot_main = RobotMain(arm)

# robot_main.run("",0)