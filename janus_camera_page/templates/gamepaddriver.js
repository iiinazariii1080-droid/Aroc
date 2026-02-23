// Lightweight gamepad → JoystickFrame driver.
// Никакой сети, только чтение Gamepad API и вызов колбэка onFrame(frame).
//
// Формат JoystickFrame:
// {
//   ts:     <Number, ms с epoch>,
//   axes:   <Array[4] of Number>,
//   buttons:<Array[19] of 0|1>,
//   ttl:    <Number, ms>
// }

(function (global) {
  "use strict";

  const AXES_COUNT = 4;
  const BUTTON_COUNT = 19;
  const ACTIVE_INTERVAL_MS = 30;    // движение
  const IDLE_INTERVAL_MS   = 120;   // ~8 Гц (короткий ноль)
  const KEEPALIVE_INTERVAL_MS = 1000; // 1 Гц (долгий ноль)

  const IDLE_TO_KEEPALIVE_MS = 1500; // через 1.5с нуля → keepalive

  const DEFAULT_INTERVAL_MS = 30;   // ~33 Hz
  const DEFAULT_DEADZONE = 0.02;    // фильтр мелкого шума
  const DEFAULT_TTL_MS = 350;       // сколько команда ещё жива
  const DEFAULT_KEEPALIVE_MS = DEFAULT_INTERVAL_MS; // мс между keepalive; 0 = не шлём keepalive, только при изменениях

  const raf =
    global.requestAnimationFrame ||
    global.webkitRequestAnimationFrame ||
    global.mozRequestAnimationFrame ||
    function (cb) { return setTimeout(cb, DEFAULT_INTERVAL_MS); };

  const DEFAULT_AXES_MAP = Array.from({ length: AXES_COUNT }, (_, i) => i);
  const DEFAULT_BUTTONS_MAP = Array.from({ length: BUTTON_COUNT }, (_, i) => i);

  let running = false;
  let lastFrame = null;
  let lastSendPerfTs = 0;
  let lastNonZeroPerfTs = 0;

  let opts = {
    intervalMs: DEFAULT_INTERVAL_MS,
    deadzone: DEFAULT_DEADZONE,
    ttlMs: DEFAULT_TTL_MS,
    keepaliveMs: DEFAULT_KEEPALIVE_MS,
    axesMap: null,
    buttonsMap: null,
    onFrame: null,
    debug: false
  };

  function dlog() {
    if (!opts.debug) return;
    // eslint-disable-next-line no-console
    console.log.apply(console, arguments);
  }
  function isNeutral(frame) {
	  const axesZero = frame.axes.every(v => v === 0);
	  const buttonsZero = frame.buttons.every(v => v === 0);
	  return axesZero && buttonsZero;
  }
  function readFirstGamepad() {
    if (!navigator.getGamepads && !navigator.webkitGetGamepads) return null;
    const gps = navigator.getGamepads
      ? navigator.getGamepads()
      : navigator.webkitGetGamepads();
    if (!gps) return null;
    for (let i = 0; i < gps.length; i++) {
      if (gps[i]) return gps[i];
    }
    return null;
  }

  function normalizeAxes(src, deadzone) {
    const map = Array.isArray(opts.axesMap) && opts.axesMap.length
      ? opts.axesMap
      : DEFAULT_AXES_MAP;
    const out = new Array(map.length);
    for (let i = 0; i < map.length; i++) {
      const srcIndex = map[i];
      let v = src && typeof src[srcIndex] === "number" ? src[srcIndex] : 0;
      if (Math.abs(v) < deadzone) v = 0;
      out[i] = +v.toFixed(3);
    }
    return out;
  }

  function normalizeButtons(src) {
    const map = Array.isArray(opts.buttonsMap) && opts.buttonsMap.length
      ? opts.buttonsMap
      : DEFAULT_BUTTONS_MAP;
    const out = new Array(map.length);
    for (let i = 0; i < map.length; i++) {
      const srcIndex = map[i];
      const b = src && src[srcIndex] != null ? src[srcIndex] : 0;
      let pressed = 0;
      if (typeof b === "number") {
        pressed = b >= 0.5 ? 1 : 0;
      } else if (typeof b === "object" && "pressed" in b) {
        pressed = b.pressed ? 1 : 0;
      }
      out[i] = pressed;
    }
    return out;
  }

  function shouldEmit(cur) {
    if (!lastFrame) return true;

    // Если оси/кнопки изменились — шлём
    if (JSON.stringify(cur.axes) !== JSON.stringify(lastFrame.axes)) return true;
    if (JSON.stringify(cur.buttons) !== JSON.stringify(lastFrame.buttons)) return true;

    // иначе раз в keepaliveMs можно отправить keepalive (если включён)
    if (!opts.keepaliveMs || opts.keepaliveMs <= 0) return false;
    return (cur.ts - lastFrame.ts) > opts.keepaliveMs;
  }

	function tick() {
	  if (!running) return;

	  try {
		const gp = readFirstGamepad();
		if (gp) {
		  const nowPerf = (global.performance && performance.now()) || Date.now();

		  const axes = normalizeAxes(gp.axes || [], opts.deadzone);
		  const buttons = normalizeButtons(gp.buttons || []);
		  const frame = {
			ts: Date.now(),
			axes,
			buttons,
			ttl: opts.ttlMs
		  };

		  const neutral = isNeutral(frame);
		  if (!neutral) {
			lastNonZeroPerfTs = nowPerf;
		  }

		  let minInterval = ACTIVE_INTERVAL_MS;
		  if (neutral) {
			const idleFor = nowPerf - lastNonZeroPerfTs;
			if (idleFor > IDLE_TO_KEEPALIVE_MS) {
			  minInterval = KEEPALIVE_INTERVAL_MS; // 1 Гц
			} else {
			  minInterval = IDLE_INTERVAL_MS; // ~8 Гц
			}
		  }

		  if (nowPerf - lastSendPerfTs >= minInterval) {
			if (shouldEmit(frame)) {
			  lastSendPerfTs = nowPerf;
			  lastFrame = frame;
			  if (typeof opts.onFrame === "function") {
				try {
				  opts.onFrame(frame, gp);
				} catch (cbErr) {
				  console.warn("[GamepadDriver] onFrame callback error:", cbErr);
				}
			  }
			}
		  }
		}
	  } catch (e) {
		console.warn("[GamepadDriver] tick error:", e);
	  }

	  raf(tick);
	}

  function start(options) {
    if (!navigator.getGamepads && !navigator.webkitGetGamepads) {
      console.warn("[GamepadDriver] Gamepad API not supported in this browser");
      return;
    }

    opts = Object.assign({}, opts, options || {});
    if (running) return;
    running = true;
    dlog("[GamepadDriver] starting with opts=", opts);
    raf(tick);
  }

  function stop() {
    running = false;
  }

  // Экспортируем в глобал
  global.GamepadDriver = {
    start,
    stop
  };
})(window);
