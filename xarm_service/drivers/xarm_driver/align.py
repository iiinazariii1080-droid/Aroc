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
import time
import traceback
from xarm import version
from xarm.wrapper import XArmAPI
import sys
import json
import time
import websocket
import socket
import base64
import numpy as np
from PIL import Image
from io import BytesIO


# Получение имени хоста
hostname = socket.gethostname()

def get_points(center_x, center_y, focal_length, depth_at_center):
    pixels_per_cm = focal_length / depth_at_center
    half_side_length_x = int(3 * pixels_per_cm)
    half_side_length_y = int(3 * pixels_per_cm)
    
    return {
        "A": {"x": center_x - half_side_length_x, "y": center_y - half_side_length_y},
        "B": {"x": center_x + half_side_length_x, "y": center_y - half_side_length_y},
        "C": {"x": center_x - half_side_length_x, "y": center_y + half_side_length_y},
        "D": {"x": center_x + half_side_length_x, "y": center_y + half_side_length_y}
    }

camera_offset = 2.3
width = 640
height = 480
center_x = width / 2
center_y = height / 2


def process_depth_data(base64_data):
    # Декодируем Base64 в бинарные данные
    binary_data = base64.b64decode(base64_data)
    data_len = len(binary_data)
    # Проверяем длину данных
    if data_len != 259200:
        return None
    # Преобразуем бинарные данные в массив байтов
    bytes_array = np.frombuffer(binary_data, dtype=np.uint8)
    return bytes_array

# Переменные для управления
max_frames = 5  # Количество кадров для получения
ws_url = f"ws://{socket.gethostname()}:9999"
data = []

def process_frames():
    # Параметры WebSocket

    received_frames = 0
    
    # История и элементы глубины
    depth_history = {"center": [], "A": [], "B": [], "C": [], "D": []}
    depth_elements = {"center": 0, "A": 0, "B": 0, "C": 0, "D": 0}
    
    def on_message(ws, message):
        nonlocal received_frames, depth_history, depth_elements
        # Обработка сообщения WebSocket
        result = process_depth_data(message)
        if result is not None:
            data.append(result)
            received_frames += 1
            if received_frames >= max_frames:
                ws.close()  # Закрываем соединение после получения 10 кадров

    def on_error(ws, error):
        print(f"WebSocket error: {error}")
    
    def on_close(ws, close_status_code, close_msg):
        print("WebSocket connection closed")
    
    def on_open(ws):
        print("WebSocket connection opened")
    
    # Создание подключения WebSocket
    ws = websocket.WebSocketApp(
        ws_url,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    
    # Запуск WebSocket
    ws.run_forever()

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

    def run(self):
        process_frames()
        try:
            if not self.is_alive:
                return True
            self._arm.set_servo_angle(angle=[j1, j2, j3, j4, j5, j6], speed=self._angle_speed, mvacc=self._angle_acc, wait=True, radius=-1.0)
        except Exception as e:
            self.pprint('MainException: {}'.format(e))

        self.alive = False
        self._arm.release_error_warn_changed_callback(self._error_warn_changed_callback)
        self._arm.release_state_changed_callback(self._state_changed_callback)
        if hasattr(self._arm, 'release_count_changed_callback'):
            self._arm.release_count_changed_callback(self._count_changed_callback)


if __name__ == '__main__':
    try:
        RobotMain.pprint('xArm-Python-SDK Version:{}'.format(version.__version__))
        arm = XArmAPI('192.168.1.220', baud_checkset=False)
        robot_main = RobotMain(arm)

        if robot_main.run():
            sys.exit(0)
    except:
        sys.exit(2)

    
