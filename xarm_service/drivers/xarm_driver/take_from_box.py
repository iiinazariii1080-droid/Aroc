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
import math
import time
import queue
import datetime
import random
import traceback
import threading
from xarm import version
from xarm.wrapper import XArmAPI




# Допустим, что у нас известны параметры звеньев (примерные данные)
link_lengths = [0.3, 0.25, 0.2]  # Длины звеньев в метрах
gravitational_acceleration = 9.81  # Ускорение свободного падения

# Пример вычисления нагрузки
def estimate_mass(servo_states, position):
    # Извлекаем токи двигателей (упрощённый пример для одного звена)
    current = servo_states[1]['current']  # Ток на втором звене
    torque_constant = 0.1  # Номинальный коэффициент момента для моторов (Nm/A)

    # Вычисляем момент
    torque = current * torque_constant

    # Вычисляем массу объекта (с учётом плеча силы)
    length = link_lengths[1]  # Длина второго звена
    mass = torque / (length * gravitational_acceleration)
    return mass

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
        if hasattr(self._arm, 'register_count_changed_callback'):
            self._arm.register_count_changed_callback(self._count_changed_callback)

    # Register error/warn changed callback
    def _error_warn_changed_callback(self, data):
        if data and data['error_code'] != 0:
            self.alive = False
            self.pprint('err={}, quit'.format(data['error_code']))
            self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)

    # Register state changed callback
    def _state_changed_callback(self, data):
        if data and data['state'] == 4:
            self.alive = False
            self.pprint('state=4, quit')
            self._arm.release_state_changed_callback(self._state_changed_callback)

    # Register count changed callback
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
            print('[{}][{}] {}'.format(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), stack_tuple[1], ' '.join(map(str, args))))
        except:
            print(*args, **kwargs)

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
    def run(self,x,y,z):
        global correct_j6
        if rotate:
            correct_j6 = - 90

        try:
            # Joint Motion
            self._angle_speed = 40
            self._angle_acc = 200
            for i in range(int(1)):
                if not self.is_alive:
                    break

                code = self._arm.set_servo_angle(angle=[62.5, 26.5, -66.9, -57.2, 15.7, 84.0+correct_j6], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=0.0)
                if not self._check_code(code, 'set_servo_angle'):
                    return
                
                try:
                    code = self._arm.set_suction_cup(True, wait=True, delay_sec=0)
                    if not self._check_code(code, 'set_suction_cup'):
                        return
                except:
                    print("suction_error")

                code = self._arm.set_tool_position(z=-z-50,y=-y,x=-x, radius=0, speed=self._tcp_speed, mvacc=self._tcp_acc, relative=True, wait=True)
                if not self._check_code(code, 'set_position'):
                    return

                code = self._arm.set_servo_angle(angle=[62.5, 26.5, -66.9, -57.2, 15.7, 84.0+correct_j6], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=0.0)
                if not self._check_code(code, 'set_servo_angle'):
                    return
                code = self._arm.set_suction_cup(False, wait=True, delay_sec=0)
                if not self._check_code(code, 'set_suction_cup'):
                    return

                
        except Exception as e:
            self.pprint('MainException: {}'.format(e))
        self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)
box_w = 270
box_h = 370

rotate = False
correct_j6 = 0

if __name__ == '__main__':
    try:
        if len(sys.argv) != 6:
            print("Ошибка: скрипт ожидает ровно 5 аргументa.")
            sys.exit(1)
        try:
            x, y, z, w, h = map(float, sys.argv[1:6])
        except ValueError:
            print("Ошибка: все аргументы должны быть числами (float).")
            sys.exit(1)

        print(f"x = {x}, y = {y}, z = {z},w = {y}, h = {z}")
        if w > box_w and w < box_h:
            rotate = True
        if h > box_h and h < box_w:
            rotate = True

        RobotMain.pprint('xArm-Python-SDK Version:{}'.format(version.__version__))
        arm = XArmAPI('192.168.1.220', baud_checkset=False)
        robot_main = RobotMain(arm)

        if robot_main.run(x,y,z):
            sys.exit(0)
    except:
        sys.exit(2)