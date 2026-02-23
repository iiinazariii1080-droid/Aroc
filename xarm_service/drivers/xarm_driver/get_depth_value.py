import asyncio
import websockets
import base64
import numpy as np
import cv2
import socket
import sys
import arduino_controller.arduino_led_controller as als
import subprocess

# Глобальные переменные
current_depth_image = None
window_name = "Depth Stream"
ws_url = f"ws://127.0.0.1:9999"

# Глобальная переменная для хранения координат выделенного прямоугольника
highlighted_rectangle = None
annotation = None

center_coordinates = None



def calculate_object_size(rect, depth_image):
    """
    Рассчитывает реальные размеры объекта в мм на основе координат прямоугольника и карты глубины.

    :param rect: Координаты прямоугольника (x, y, w, h)
    :param depth_image: Текущая глубинная карта (numpy array)
    :return: Ширина и высота объекта в мм
    """
    x, y, w, h = rect
    # Параметры камеры
    fx = 380.4253845214844
    fy = 380.4253845214844
    depth_scale = 0.9  # Масштаб глубины (для перевода в мм, например, 1мм = 0.001)
    
    region = depth_image[y:y + h, x:x + w]

    max_value = np.max(region)

    # Разбиваем максимальное значение на 20 диапазонов
    num_ranges = 5
    range_step = max_value / num_ranges
    ranges = [(i * range_step, (i + 1) * range_step) for i in range(num_ranges)]

    # Считаем, сколько значений попало в каждый диапазон
    range_counts = {range_tuple: 0 for range_tuple in ranges}
    for value in region.flatten():
        for range_tuple in ranges:
            if range_tuple[0] <= value <= range_tuple[1]:
                range_counts[range_tuple] += 1
                break

    # Ищем диапазон с максимальным количеством значений
    max_range = max(range_counts, key=range_counts.get)


    # Разбиваем этот диапазон на 20 поддиапазонов
    sub_range_step = (max_range[1] - max_range[0]) / 5
    sub_ranges = [(max_range[0] + i * sub_range_step, max_range[0] + (i + 1) * sub_range_step) for i in range(5)]

    # Считаем количество значений в каждом поддиапазоне
    sub_range_counts = {sub_range: 0 for sub_range in sub_ranges}
    for value in region.flatten():
        if max_range[0] <= value < max_range[1]:  # Проверяем, попадает ли значение в основной диапазон
            for sub_range in sub_ranges:
                if sub_range[0] <= value < sub_range[1]:
                    sub_range_counts[sub_range] += 1
                    break

    # Ищем поддиапазон с максимальным количеством значений
    max_range = max(sub_range_counts, key=sub_range_counts.get)

    # Разбиваем этот диапазон на 20 поддиапазонов
    sub_range_step = (max_range[1] - max_range[0]) / 5
    sub_ranges = [(max_range[0] + i * sub_range_step, max_range[0] + (i + 1) * sub_range_step) for i in range(5)]

    # Считаем количество значений в каждом поддиапазоне
    sub_range_counts = {sub_range: 0 for sub_range in sub_ranges}
    for value in region.flatten():
        if max_range[0] <= value < max_range[1]:  # Проверяем, попадает ли значение в основной диапазон
            for sub_range in sub_ranges:
                if sub_range[0] <= value < sub_range[1]:
                    sub_range_counts[sub_range] += 1
                    break

    # Ищем поддиапазон с максимальным количеством значений
    max_sub_range = max(sub_range_counts, key=sub_range_counts.get)

    # Теперь усредняем значения, попавшие в этот поддиапазон
    values_in_max_sub_range = region[(region >= max_sub_range[0]) & (region < max_sub_range[1])]
    average_value_in_sub_range = np.mean(values_in_max_sub_range)

    # Умножаем на коэффициент масштабирования
    object_depth = average_value_in_sub_range * depth_scale


    # Реальные размеры объекта
    width_mm = (w * object_depth) / fx
    height_mm = (h * object_depth) / fy

    return width_mm, height_mm

def mouse_callback(current_depth_image, x, y):
    global center_coordinates
    depth_value = int(current_depth_image[y, x])  # Приведение к целому числу


    # Выделение объекта на основе глубины
    threshold = 10  # Порог для выделения объекта
    lower_bound = int(max(0, depth_value - threshold))  # Преобразование в int
    upper_bound = int(depth_value + threshold)

    # Создаем маску на основе диапазона глубины
    mask = cv2.inRange(current_depth_image, lower_bound, upper_bound)

    # Поиск контуров для выделения объекта
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # Рисуем прямоугольник вокруг объекта
    for contour in contours:
        if cv2.pointPolygonTest(contour, (x, y), False) >= 0:
            x, y, w, h = cv2.boundingRect(contour)
            highlighted_rectangle = (x, y, w, h)  # Сохраняем координаты прямоугольника
            # Рассчитываем размеры объекта
            width_mm, height_mm = calculate_object_size(highlighted_rectangle, current_depth_image)
            ret = (x,y,w,h,depth_value,width_mm,height_mm)
            # Рассчитываем центр прямоугольника
            center_x = x + w // 2
            center_y = y + h // 2
            center_coordinates = (center_x, center_y)
            return ret


from websocket import create_connection



def get_depth():
    global ws_url
    capture_analize_results = []
    """
    Постоянно получает и отображает глубинные кадры через WebSocket.
    :param ws_url: URL WebSocket-сервера (например, "ws://localhost:9999")
    """
    try:
        # Устанавливаем соединение с WebSocket
        websocket = create_connection(ws_url)
        als.send_command(5)
        while True:
            try:
                # Получаем сообщение с глубинными данными
                message = websocket.recv()
                if len(message) != 819200:
                    continue
                # Декодируем данные из Base64
                depth_bytes = base64.b64decode(message)
                # Преобразуем байты в NumPy массив (глубинные данные)
                depth_array = np.frombuffer(depth_bytes, dtype=np.uint16)
                depth_image = depth_array.reshape((480, 640))
                data = mouse_callback(depth_image,320,240)
                capture_analize_results.append(data)
                if len(capture_analize_results) >= 5:
                    # Находим максимальное и минимальное значение шестого элемента
                    max_7th = max(row[6] for row in capture_analize_results)
                    min_7th = min(row[6] for row in capture_analize_results)

                    # Фильтруем строки, исключая те, где шестой элемент максимален или минимален
                    filtered_data = [
                        row for row in capture_analize_results
                        if row[6] != max_7th and row[6] != min_7th
                    ]
                    # Находим максимальное и минимальное значение шестого элемента
                    max_7th = max(row[6] for row in filtered_data)
                    min_7th = min(row[6] for row in filtered_data)

                    # Фильтруем строки, исключая те, где шестой элемент максимален или минимален
                    filtered_data = [
                        row for row in filtered_data
                        if row[6] != max_7th and row[6] != min_7th
                    ]

                    # Для каждого индекса вычисляем среднее значение
                    averages = [sum(column) / len(column) for column in zip(*filtered_data)]
                    break
            except Exception as e:
                als.send_command(2)
                break
    finally:
        websocket.close()
        als.send_command(1)
        return averages[4]-80
    

# print(get_depth())