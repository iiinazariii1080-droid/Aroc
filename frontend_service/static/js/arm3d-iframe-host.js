(function () {
  const IFRAME_ID = "arm3d-isolated-iframe";
  const CONTAINER_SELECTOR = ".float-arm .xarm-container .arm3d";
  const IFRAME_SRC = "/arm3d_viewer?axes=all&camera_frustum&depth_cloud&transform&fallback=0&ignore_mount=1&v=20260220at";
  const PUSH_INTERVAL_MS = 100;
  const GATE_TIMEOUT_MS = 12000;
  let pushTimer = null;
  let viewerReady = false;
  let gateOpened = false;
  let gateStartedAtMs = 0;

  function normalizeEmbeddedArmStyles(container) {
    if (!container) {
      return;
    }

    const xarmContainer = container.closest(".xarm-container");
    if (xarmContainer) {
      xarmContainer.style.padding = "0";
      xarmContainer.style.background = "transparent";
      xarmContainer.style.backgroundImage = "none";
      xarmContainer.style.borderRadius = "0";
    }

    const header = container.parentElement && container.parentElement.querySelector(".header");
    if (header) {
      header.style.display = "none";
    }

    const legacyRoots = [
      container.querySelector("#model-wrapper"),
      container.querySelector(".hello"),
      container.querySelector("#model-container"),
      container.querySelector("#model-tools"),
    ];
    legacyRoots.forEach((el) => {
      if (el) {
        el.style.display = "none";
        el.style.visibility = "hidden";
        el.style.pointerEvents = "none";
      }
    });
  }

  function getViewerWindow() {
    const iframe = document.getElementById(IFRAME_ID);
    return iframe && iframe.contentWindow ? iframe.contentWindow : null;
  }

  function normalizeNumberArray(values) {
    if (!Array.isArray(values)) {
      return [];
    }
    return values
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value));
  }

  function collectArmSnapshot() {
    const globalModel = window.GlobalUtil && window.GlobalUtil.model;
    const robotState = globalModel && globalModel.robot && globalModel.robot.state;
    const info = robotState && robotState.info ? robotState.info : {};
    const remote = robotState && robotState.remote ? robotState.remote : {};
    const local = robotState && robotState.local ? robotState.local : {};

    const joints = normalizeNumberArray(remote.joints && remote.joints.length ? remote.joints : local.joints);
    const mountDegrees = normalizeNumberArray(info.xarm_mount_degrees);
    const axis = Number(info.xarm_axis);
    const type = Number(info.xarm_device_type);
    const lift = Number(window.api && window.api.igus ? window.api.igus.position : 0);
    const connected = robotState ? robotState.connected : undefined;
    const online = robotState ? robotState.online : undefined;
    const connecting = robotState
      ? (robotState.xarm_is_connecting ?? info.xarm_is_connecting)
      : undefined;

    return {
      axis: [5, 6, 7].includes(axis) ? axis : 6,
      type: Number.isFinite(type) ? type : 6,
      joints,
      mountDegrees,
      endEffector: String(info.xarm_other_tool_type || "xarm_vacuum_gripper"),
      lift: Number.isFinite(lift) ? lift : 0,
      connected,
      online,
      connecting,
      timestamp: Date.now(),
    };
  }

  function isReadySnapshot(snapshot) {
    if (!snapshot) {
      return false;
    }
    const hasIdentity = Number.isFinite(snapshot.axis) && Number.isFinite(snapshot.type);
    const hasJoints = Array.isArray(snapshot.joints) && snapshot.joints.length > 0;
    const connected = snapshot.connected !== false;
    const online = snapshot.online !== false;
    const connecting = snapshot.connecting === true;
    return hasIdentity && hasJoints && connected && online && !connecting;
  }

  function postToViewer(type, payload) {
    const viewerWindow = getViewerWindow();
    if (!viewerWindow) {
      return;
    }
    viewerWindow.postMessage({ type, payload }, window.location.origin);
  }

  function pushInitWithSnapshot(snapshot) {
    const current = snapshot || collectArmSnapshot();
    postToViewer("arm3d:init", {
      axis: current.axis,
      type: current.type,
      mountDegrees: current.mountDegrees,
      endEffector: current.endEffector,
    });
    postToViewer("arm3d:config", { fallbackPolling: false, ignoreMount: true });
  }

  function pushStateWithSnapshot(snapshot) {
    const current = snapshot || collectArmSnapshot();
    postToViewer("arm3d:state", {
      joints: current.joints,
      lift: current.lift,
      timestamp: current.timestamp,
    });
  }

  function maybeOpenGateAndPush(snapshot) {
    if (gateOpened) {
      return;
    }
    const timedOut = gateStartedAtMs > 0 && (Date.now() - gateStartedAtMs) >= GATE_TIMEOUT_MS;
    if (!isReadySnapshot(snapshot) && !timedOut) {
      return;
    }
    gateOpened = true;
    pushInitWithSnapshot(snapshot);
    pushStateWithSnapshot(snapshot);
  }

  function ensurePushLoop() {
    if (pushTimer) {
      return;
    }
    pushTimer = window.setInterval(() => {
      if (!viewerReady) {
        return;
      }
      const snapshot = collectArmSnapshot();
      maybeOpenGateAndPush(snapshot);
      if (!gateOpened) {
        return;
      }
      pushStateWithSnapshot(snapshot);
    }, PUSH_INTERVAL_MS);
  }

  function ensureIframe(container) {
    if (!container) {
      return false;
    }

    normalizeEmbeddedArmStyles(container);

    let iframe = document.getElementById(IFRAME_ID);
    if (iframe && iframe.parentElement !== container) {
      iframe.remove();
      iframe = null;
    }

    if (!iframe) {
      iframe = document.createElement("iframe");
      iframe.id = IFRAME_ID;
      iframe.src = IFRAME_SRC;
      iframe.title = "Arm3D Viewer";
      iframe.setAttribute("frameborder", "0");
      iframe.style.position = "absolute";
      iframe.style.inset = "0";
      iframe.style.width = "100%";
      iframe.style.height = "100%";
      iframe.style.border = "none";
      iframe.style.borderRadius = "0";
      iframe.style.background = "transparent";
      iframe.style.zIndex = "30";
      iframe.style.display = "block";
      container.appendChild(iframe);
    }

    container.style.position = "relative";
    container.style.overflow = "hidden";
    container.style.background = "transparent";
    container.style.borderRadius = "0";
    return true;
  }

  function attach() {
    const container = document.querySelector(CONTAINER_SELECTOR);
    return ensureIframe(container);
  }

  function boot() {
    attach();

    const observer = new MutationObserver(function () {
      attach();
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    window.addEventListener("message", function (event) {
      if (!event || event.origin !== window.location.origin || !event.data) {
        return;
      }

      if (event.data.type === "arm3d:ready") {
        viewerReady = true;
        if (!gateStartedAtMs) {
          gateStartedAtMs = Date.now();
        }
        const container = document.querySelector(CONTAINER_SELECTOR);
        if (container) {
          container.setAttribute("data-arm3d-iframe", "ready");
        }
        const snapshot = collectArmSnapshot();
        maybeOpenGateAndPush(snapshot);
      }

      if (event.data.type === "arm3d:requestSnapshot") {
        viewerReady = true;
        if (!gateStartedAtMs) {
          gateStartedAtMs = Date.now();
        }
        const snapshot = collectArmSnapshot();
        maybeOpenGateAndPush(snapshot);
        if (gateOpened) {
          pushStateWithSnapshot(snapshot);
        }
      }
    });

    ensurePushLoop();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
