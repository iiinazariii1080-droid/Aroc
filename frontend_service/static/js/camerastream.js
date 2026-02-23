const camera_offset = 2.5;
const width = 640;
const height = 480;
const centerX = width / 2, centerY = height / 2;
const depthElements = {
    center: 0,
    A: 0,
    B: 0,
    C: 0,
    D: 0
};

let scaledArray = [];

const depthHistory = {
    center: [],
    A: [],
    B: [],
    C: [],
    D: []
};

async function requestDepth(x, y) {
    // Базовый URL к твоему прокси
    const base = `${window.location.protocol}//${window.location.hostname}:${window.location.port}/api/v1/depth_camera/depth`;
  
    // Формируем URL и кодируем JSON в параметр message
    const url = new URL(base);
    url.searchParams.set("message", JSON.stringify({ x, y }));
  
    try {
      const resp = await fetch(url.toString(), { method: "GET" });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status} ${resp.statusText} ${text}`);
      }
      const data = await resp.json(); // API возвращает {"type":"depth","x":50.0,"y":50.0,"depth":302.0}
      return { depth: data.depth }; // Преобразуем в ожидаемый формат
    } catch (e) {
      console.error("requestDepth error:", e);
      return { depth: -1, error: String(e.message || e) };
    }
  }

  
// Функция для установки соединения для depth потока
function connectDepthSocket() {
    const depthSocket =  new WebSocket('ws://' + location.hostname + ':' + location.port + '/depth');
    depthSocket.binaryType = 'arraybuffer';
    
    depthSocket.onopen = () => {
        console.log('Depth WebSocket connection established.');
    };
    
    depthSocket.onmessage = (event) => {
        const color_canvas = document.getElementById('camera1Canvas');
        if (color_canvas == null) {
            return;
        }
        const depthArray = processDepthData(event.data, color_canvas);
        if (depthArray == 0) {
            return;
        }
        scaledArray = scaleDepthArray(depthArray, 640, 480, width, height);
        
        const currentCenter = getDepthAtPoint(scaledArray, centerX, centerY, width) - camera_offset;
        const points = getPoints(240, 320, 380.4253845214844, currentCenter.depth);
    
        // Получаем текущие глубины для точек A, B, C и D
        const currentDepths = {
            center: currentCenter.depth,
            A: getDepthAtPoint(scaledArray, points.A.x, points.A.y, width) - camera_offset,
            B: getDepthAtPoint(scaledArray, points.B.x, points.B.y, width) - camera_offset,
            C: getDepthAtPoint(scaledArray, points.C.x, points.C.y, width) - camera_offset,
            D: getDepthAtPoint(scaledArray, points.D.x, points.D.y, width) - camera_offset
        };
    
        // Добавляем текущие значения в историю и удаляем старые
        for (let key in depthHistory) {
            depthHistory[key].push(currentDepths[key]);
            if (depthHistory[key].length > 5) {
                depthHistory[key].shift();
            }
        }
    
        // Вычисляем среднее значение для каждой точки из последних 5 кадров
        for (let key in depthElements) {
            depthElements[key] = depthHistory[key].reduce((sum, value) => sum + value, 0) / depthHistory[key].length;
        }
    
        const depth_canvas = document.getElementById('depthCanvas');
        setCanvasDimensions(depth_canvas);
        const ctx = depth_canvas.getContext('2d');
        updateCanvasWithPoints(ctx, points, scaledArray, depth_canvas);
        displayInfo(ctx, depthElements.center, depth_canvas.width);
    };
    
    depthSocket.onclose = () => {
        console.log('Depth WebSocket connection closed. Attempting to reconnect in 5 seconds...');
        setTimeout(connectDepthSocket, 5000);
    };
    
    return depthSocket;
}

// Функция для установки соединения для цветного потока
function connectColorSocket() {
    const colorSocket = new WebSocket('ws://' + location.hostname + ':' + location.port + '/color');
    
    colorSocket.onopen = () => {
        console.log('Color WebSocket connection established.');
    };
    
    colorSocket.onmessage = (event) => {
        const color_canvas = document.getElementById('camera2Canvas');
        if (color_canvas == null) {
            return;
        }
        processDepthData(event.data, color_canvas);
    };
    
    colorSocket.onclose = () => {
        console.log('Color WebSocket connection closed. Attempting to reconnect in 5 seconds...');
        setTimeout(connectColorSocket, 5000);
    };
    
    return colorSocket;
}

// Инициализируем соединения
let camera1_depth_socket = connectDepthSocket();
let camera2_color_socket = connectColorSocket();

function getPoints(centerX, centerY, focalLength, depthAtCenter) {
    const pixelsPerCm = focalLength / depthAtCenter;
    const halfSideLengthX = parseInt(1.5 * pixelsPerCm);
    const halfSideLengthY = parseInt(1.5 * pixelsPerCm);
    return {
        A: { x: centerX - halfSideLengthX, y: centerY - halfSideLengthY },
        B: { x: centerX + halfSideLengthX, y: centerY - halfSideLengthY },
        C: { x: centerX - halfSideLengthX, y: centerY + halfSideLengthY },
        D: { x: centerX + halfSideLengthX, y: centerY + halfSideLengthY }
    };
}
window.cameraStream = {
    // Возвращает Promise, который резолвится объектом с ключами: center, A, B, C, D.
    getDepth: async function() {
      const centerX = 240, centerY = 320;
      const focalLength = 380.4253845214844;
      try {
        // Запрос глубины для центральной точки
        const center = await requestDepth(50, 50);
        // Вычисляем координаты остальных точек на основе центральной глубины
        const points = getPoints(240, 320, focalLength, center.depth);
        // Параллельно запрашиваем глубину для точек A, B, C и D
        const [a, b, c, d] = await Promise.all([
          requestDepth(60, 50),
          requestDepth(40, 60),
          requestDepth(50, 45),
          requestDepth(50, 55)
        ]);
        return { A: (a.depth/10)-camera_offset, B: (b.depth/10)-camera_offset, C: (c.depth/10)-camera_offset, D: (d.depth/10)-camera_offset, X: (center.depth/10)-camera_offset  };
      } catch (e) {
        console.error("Ошибка получения глубины:", e);
        throw e;
      }
    },
    getOnePoint: async function(cX,cY) {
        const centerX = 50, centerY = 50;
        const focalLength = 380.4253845214844;
        try {
          // Запрос глубины для центральной точки
          const centerDepth = await requestDepth(centerX, centerY);
          return { centerDepth: centerDepth};
        } catch (e) {
          console.error("Ошибка получения глубины:", e);
          throw e;
        }
      }    
  };


function processDepthData(base64Data, canvas) {
    const binaryData = atob(base64Data);
    const len = binaryData.length;
    if (len !== 614400) {
        const img = new Image();
        const ctx1 = canvas.getContext('2d');
        
        // Когда изображение загрузится, рисуем его на холсте
        img.onload = function () {
            ctx1.drawImage(img, 0, 0, canvas.width, canvas.height);
        };
    
        // Устанавливаем src изображения в base64 формате
        img.src = 'data:image/jpeg;base64,' + base64Data;
    
        return 0; // Пропускаем сообщение
    }
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binaryData.charCodeAt(i);
    }
    return new Uint16Array(bytes.buffer);
}




function scaleDepthArray(depthArray, originalWidth, originalHeight, newWidth, newHeight) {
    const scaledArray = new Uint16Array(newWidth * newHeight);
    const scaleX = originalWidth / newWidth;
    const scaleY = originalHeight / newHeight;
    
    for (let y = 0; y < newHeight; y++) {
        for (let x = 0; x < newWidth; x++) {
            // Рассчитываем исходную позицию для текущего пикселя
            const originalX = Math.floor(x * scaleX);
            const originalY = Math.floor(y * scaleY);
            
            // Копируем значение пикселя из оригинального массива
            const pixel = depthArray[originalY * originalWidth + originalX];
            
            // Заполняем соответствующий пиксель в новом изображении
            scaledArray[y * newWidth + x] = pixel;
        }
    }
    
    return scaledArray;
}
function setCanvasDimensions(canvas) {
    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;
}
function getDepthAtPoint(depthArray, x, y, width) {
    if (x < 0 || x >= width || y < 0 || y >= depthArray.length / width) {
        console.error(`Coordinates out of bounds: x=${x}, y=${y}`);
        return NaN;
    }
    
    const depthValue = depthArray[y * width + x];
    if (depthValue === undefined || depthValue === null) {
        console.error(`Depth value is undefined or null at x=${x}, y=${y}`);
        return 0;
    }
    
    return depthValue / 10;
}
function updateCanvasWithPoints(ctx, points, depthArray, canvas) {
    // Фиксированный размер изображения
    const imageWidth = width;
    const imageHeight = height;

    // Создаем ImageData для depth изображения
    const imageData = ctx.createImageData(imageWidth, imageHeight);
    const normalizedImage = new Uint8ClampedArray(imageWidth * imageHeight);

    for (let i = 0; i < depthArray.length; i++) {
        normalizedImage[i] = (depthArray[i] / 256) * 100;
        const index = i * 4;
        imageData.data[index] = 255 - normalizedImage[i];  // R
        imageData.data[index + 1] = 255 - normalizedImage[i];  // G
        imageData.data[index + 2] = 255 - normalizedImage[i];  // B
        imageData.data[index + 3] = 255;  // Alpha
    }
    // Временное canvas для масштабирования изображения и отрисовки точек
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = imageWidth;
    tempCanvas.height = imageHeight;
    const tempCtx = tempCanvas.getContext('2d');

    // Отображаем imageData на временное canvas
    tempCtx.putImageData(imageData, 0, 0);

    // Определяем глубину центра и допустимую погрешность в 5%
    const centerDepth = depthElements.center;
    const tolerance = 0.2;

    // Рисуем точки на временном canvas
    Object.entries(points).forEach(([key, point]) => {
        // Проверяем, находится ли глубина точки в пределах ±5% от глубины центра
        const isWithinTolerance = Math.abs(depthElements[key] - centerDepth) <= tolerance;

        // Устанавливаем цвет в зависимости от сравнения с глубиной центра
        tempCtx.fillStyle = isWithinTolerance ? 'green' : 'blue';
        tempCtx.beginPath();
        tempCtx.arc(point.x, point.y, 5, 0, 2 * Math.PI);
        tempCtx.fill();
    });

    // Очищаем основной canvas и масштабируем временный canvas на него
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(tempCanvas, 0, 0, canvas.width, canvas.height);
}
function displayInfo(ctx, depthAtCenter, canvasWidth) {
    ctx.fillStyle = 'red';
    ctx.font = '12px Arial';
    dcv = depthAtCenter 
    if (dcv<0) {dcv = 0} 
    const text2 = `A: ${depthElements.A.toFixed(2)} cm`;
    ctx.fillText(text2, canvasWidth - ctx.measureText(text2).width,  20);
    const text3 = `B: ${depthElements.B.toFixed(2)} cm`;
    ctx.fillText(text3, canvasWidth - ctx.measureText(text3).width,  30);

    const text4 = `C: ${depthElements.C.toFixed(2)} cm`;
    ctx.fillText(text4, canvasWidth - ctx.measureText(text4).width,  40);
    const text5 = `D: ${depthElements.D.toFixed(2)} cm`;
    ctx.fillText(text5, canvasWidth - ctx.measureText(text5).width,  50);
}

