import asyncio
import websockets
import base64
import numpy as np
import cv2
import socket
import subprocess

# Глобальные переменные
current_depth_image = None
highlighted_rectangle = None  # Координаты прямоугольника
object_text = ""  # Текст аннотации
window_name = "Depth Stream"
ws_url = f"ws://{socket.gethostname()}:9999"
center_coordinates = None  # Координаты центра квадрата

def calculate_camera_shift(center_x, center_y, object_x, object_y, depth_image, fx, fy):
    """
    Рассчитывает, на сколько нужно переместить объектив камеры по x и y,
    чтобы выровнять центр изображения с центром объекта.

    :param center_x: X-координата центра изображения (пиксели)
    :param center_y: Y-координата центра изображения (пиксели)
    :param object_x: X-координата центра объекта (пиксели)
    :param object_y: Y-координата центра объекта (пиксели)
    :param object_depth: Глубина объекта (мм)
    :param fx: Фокусное расстояние по оси X
    :param fy: Фокусное расстояние по оси Y
    :return: Смещение камеры по осям X и Y в мм
    """
    # Вычисляем смещение в пикселях
    delta_x_pixels = object_x - center_x
    delta_y_pixels = object_y - center_y
    depth_value = int(depth_image[object_y, object_x])
    # Переводим смещение из пикселей в миллиметры
    delta_x_mm = (delta_x_pixels * depth_value) / fx
    delta_y_mm = (delta_y_pixels * depth_value) / fy

    return delta_x_mm, delta_y_mm

def calculate_distance_between_points(x1, y1, x2, y2, depth_image):
    """
    Рассчитывает расстояние между двумя точками в миллиметрах с учетом глубины.

    :param x1, y1: Координаты первой точки
    :param x2, y2: Координаты второй точки
    :param depth_image: Глубинное изображение
    :return: Расстояние между точками в мм
    """
    # Масштаб глубины (для перевода в миллиметры)
    depth_scale = 0.9  # Подгоните в зависимости от вашей камеры
    
    # Фокусное расстояние (подберите под параметры вашей камеры)
    fx = 380.4253845214844
    fy = 380.4253845214844

    # Извлекаем глубину для каждой точки
    z1 = depth_image[y1, x1] * depth_scale
    z2 = depth_image[y2, x2] * depth_scale

    # Переводим координаты пикселей в 3D-пространство
    x1_world = (x1 * z1) / fx
    y1_world = (y1 * z1) / fy
    x2_world = (x2 * z2) / fx
    y2_world = (y2 * z2) / fy

    # Рассчитываем евклидово расстояние между точками в 3D
    distance = np.sqrt((x2_world - x1_world) ** 2 +
                       (y2_world - y1_world) ** 2 +
                       (z2 - z1) ** 2)

    return distance


def calculate_object_size(rect, depth_image):
    """
    Рассчитывает размеры объекта в миллиметрах.

    :param rect: Координаты прямоугольника (x, y, w, h)
    :param depth_image: Глубинное изображение
    :return: ширина, высота в мм
    """
    x, y, w, h = rect
    distances = depth_image[y:y + h, x:x + w]
    depth_scale = 0.9
    object_depth = np.mean(distances) * depth_scale
    
    # focal = 380.4253845214844
    focal = 400
    fx, fy = 380.4253845214844, 380.4253845214844

    # Рассчитываем размеры объекта
    width_mm = (w * object_depth) / fx
    height_mm = (h * object_depth) / fy
    return width_mm, height_mm

def update_rectangle_position(rect, delta_x_mm, delta_y_mm,depth_image, fx, fy):
    global highlighted_rectangle, center_coordinates
    """
    Обновляет координаты рамки объекта после перемещения камеры.

    :param rect: Координаты рамки (x, y, w, h)
    :param delta_x_mm: Смещение камеры по X в мм
    :param delta_y_mm: Смещение камеры по Y в мм
    :param object_depth: Глубина объекта (мм)
    :param fx: Фокусное расстояние по оси X
    :param fy: Фокусное расстояние по оси Y
    :return: Новые координаты рамки (x, y, w, h)
    """
    x, y, w, h = rect
    distances = depth_image[y:y + h, x:x + w]
    depth_scale = 0.9
    object_depth = np.mean(distances) * depth_scale


    # Переводим смещение камеры из мм в пиксели
    delta_x_pixels = int((delta_x_mm * fx) / object_depth)
    delta_y_pixels = int((delta_y_mm * fy) / object_depth)

    # Обновляем координаты рамки
    new_x = x - delta_x_pixels
    new_y = y - delta_y_pixels
    center_x = new_x + w // 2
    center_y = new_y + h // 2
    center_coordinates = (center_x, center_y)
    highlighted_rectangle = (new_x,new_y,w,h)

def mouse_callback(event, x, y, flags, param):
    """
    Обработчик событий мыши для отображения глубины выбранного пикселя.
    """
    global current_depth_image, highlighted_rectangle, object_text, center_coordinates,start_flag

    if event == cv2.EVENT_LBUTTONDOWN and current_depth_image is not None:
        start_flag = False
        # Получаем глубину по координатам (y, x)
        depth_value = int(current_depth_image[y, x])
        print(f"Depth at pixel ({x}, {y}): {depth_value} mm")

        # Выделение объекта на основе глубины
        threshold = 10
        lower_bound = int(max(0, depth_value - threshold))
        upper_bound = int(depth_value + threshold)

        # Создаем маску на основе диапазона глубины
        mask = cv2.inRange(current_depth_image, lower_bound, upper_bound)

        # Поиск контуров для выделения объекта
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Ищем контур с кликом и вычисляем размеры
        for contour in contours:
            if cv2.pointPolygonTest(contour, (x, y), False) >= 0:
                x, y, w, h = cv2.boundingRect(contour)
                highlighted_rectangle = (x, y, w, h)  # Сохраняем координаты прямоугольника

                # Рассчитываем размеры объекта
                width_mm, height_mm = calculate_object_size(highlighted_rectangle, current_depth_image)
                object_text = f"Width: {width_mm:.2f} mm, Height: {height_mm:.2f} mm"
                # Рассчитываем центр прямоугольника
                center_x = x + w // 2
                center_y = y + h // 2
                center_coordinates = (center_x, center_y)
                print(object_text)
                print("Rec Center: "+ str(center_coordinates))
                break
start_flag = False
async def stream_depth_frames(ws_url):
    """
    Постоянно получает и отображает глубинные кадры через WebSocket.
    """
    global current_depth_image, highlighted_rectangle, object_text,center_coordinates,start_flag

    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, mouse_callback)

    async with websockets.connect(ws_url) as websocket:
        print("Connected to WebSocket server...")

        while True:
            try:
                # Получаем сообщение с глубинными данными
                message = await websocket.recv()
                if len(message) != 819200:
                    continue

                # Декодируем данные из Base64
                depth_bytes = base64.b64decode(message)

                # Преобразуем байты в NumPy массив
                depth_array = np.frombuffer(depth_bytes, dtype=np.uint16)

                # Изменяем размер массива
                depth_image = depth_array.reshape((480, 640))

                # Сохраняем текущее глубинное изображение
                current_depth_image = depth_image

                # Визуализируем глубинное изображение
                depth_colormap = cv2.applyColorMap(
                    cv2.convertScaleAbs(depth_image, alpha=0.03), cv2.COLORMAP_JET
                )

                # Если есть выделенный прямоугольник, рисуем его и текст
                cv2.circle(depth_colormap, (320, 240), 5, (0, 0, 255), -1)
                if highlighted_rectangle is not None:
                    x, y, w, h = highlighted_rectangle
                    cv2.rectangle(depth_colormap, (x, y), (x + w, y + h), (0, 0, 255), 2)
                    # Рисуем диагонали
                    cv2.line(depth_colormap, (x, y), (x + w, y + h), (0, 255, 0), 1)
                    cv2.line(depth_colormap, (x + w, y), (x, y + h), (0, 255, 0), 1)

                    # Рисуем центр
                    if center_coordinates is not None:
                        cv2.circle(depth_colormap, center_coordinates, 5, (255, 255, 255), -1)
                        # Рассчитываем смещение камеры
                        if start_flag == False:
                            start_flag = True

                            delta_x_mm, delta_y_mm = calculate_camera_shift(320, 240, center_coordinates[0], center_coordinates[1], depth_image, 380.4253845214844, 380.4253845214844)
                            print(f"Смещение камеры по X: {delta_x_mm:.2f} мм, по Y: {delta_y_mm:.2f} мм")
                            script_path = '/home/boris/web_server/xarm/xarm_scripts/move_tool_position.py'
                            try: 
                                args = [-delta_y_mm, delta_x_mm, 0]
                                result = subprocess.run(
                                    ["/bin/python", script_path, *map(str, args)],
                                    capture_output=True,
                                    text=True,
                                    check=True
                                )
                            except:
                                continue
                            update_rectangle_position(highlighted_rectangle,delta_x_mm,delta_y_mm, depth_image, 380.4253845214844, 380.4253845214844)
                            
                    cv2.putText(
                        depth_colormap,
                        object_text,
                        (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                    )

                cv2.imshow(window_name, depth_colormap)

                # Закрываем окно при нажатии клавиши "q"
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    print("Streaming stopped by user.")
                    break
            except Exception as e:
                print(f"Error while receiving data: {e}")
                break

    # Закрываем окно OpenCV
    cv2.destroyAllWindows()

# Запуск трансляции
async def main():
    await stream_depth_frames(ws_url)

# Запуск асинхронного клиента
asyncio.run(main())
