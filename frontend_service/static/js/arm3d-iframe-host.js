(function () {
  const IFRAME_ID = "arm3d-isolated-iframe";
  const CONTAINER_SELECTOR = ".float-arm .xarm-container .arm3d";

  // Toggle between legacy (/arm3d_viewer) and v2 (/arm3d_v2) viewer.
  // v2 is enabled by default; set false only for emergency rollback.
  const USE_V2 = true;
  const IFRAME_SRC = USE_V2
    ? "/arm3d_v2?axes=all&camera_frustum&depth_cloud&transform&fallback=0&v=20260226_mount"
    : "/arm3d_viewer?axes=all&camera_frustum&depth_cloud&transform&fallback=0&v=20260226_mount";

  const PUSH_INTERVAL_MS = 100;
  let pushTimer = null;
  let viewerReady = false;
  let gateOpened = false;
  let bootstrapSeq = 0;
  let bootstrapAcked = false;
  let messageSeq = 0;
  const MAX_BOOT_LOG = 2000;

  const TRACE_ENABLED = /(?:^|[?&])trace(?:=1|=true|&|$)/i.test(window.location.search || "");

  function traceBoot(marker, payload) {
    const entry = {
      ts: Date.now(),
      marker,
      payload: typeof payload === "undefined" ? null : payload,
    };
    const store = window.__arm3dBootLog = Array.isArray(window.__arm3dBootLog)
      ? window.__arm3dBootLog
      : [];
    store.push(entry);
    if (store.length > MAX_BOOT_LOG) {
      store.splice(0, store.length - MAX_BOOT_LOG);
    }

    if (!TRACE_ENABLED) {
      return;
    }
    if (typeof payload === "undefined") {
      console.log("[ARM3D_HOST]", marker);
      return;
    }
    console.log("[ARM3D_HOST]", marker, payload);
  }

  function summarizePayload(payload) {
    if (!payload || typeof payload !== "object") {
      return payload;
    }
    const out = { ...payload };
    if (Array.isArray(out.joints)) {
      out.jointsCount = out.joints.length;
      out.jointsPreview = out.joints.slice(0, 6);
    }
    return out;
  }

  function setContainerState(name, value) {
    const container = document.querySelector(CONTAINER_SELECTOR);
    if (container) {
      container.setAttribute(name, value);
    }
  }

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

  function pickMountDegrees(info, remote, local) {
    const candidates = [
      info && info.xarm_mount_degrees,
      remote && remote.xarm_mount_degrees,
      local && local.xarm_mount_degrees,
      Array.isArray(remote && remote.data) ? remote.data[51] : null,
      Array.isArray(local && local.data) ? local.data[51] : null,
      window.api && window.api.xarm ? window.api.xarm.xarm_mount_degrees : null,
      Array.isArray(window.api && window.api.xarm && window.api.xarm.data) ? window.api.xarm.data[51] : null,
    ];

    for (const c of candidates) {
      const arr = normalizeNumberArray(c);
      if (arr.length >= 2) {
        return [arr[0], arr[1]];
      }
    }
    return null;
  }

  function collectArmSnapshot() {
    const globalModel = window.GlobalUtil && window.GlobalUtil.model;
    const robotState = globalModel && globalModel.robot && globalModel.robot.state;
    const info = robotState && robotState.info ? robotState.info : {};
    const remote = robotState && robotState.remote ? robotState.remote : {};
    const local = robotState && robotState.local ? robotState.local : {};

    const joints = normalizeNumberArray(remote.joints && remote.joints.length ? remote.joints : local.joints);
    const mountDegrees = pickMountDegrees(info, remote, local);
    const axis = Number(info.xarm_axis);
    const type = Number(info.xarm_device_type);
    const lift = Number(window.api && window.api.igus ? window.api.igus.position : 0);
    const connected = robotState ? robotState.connected : undefined;
    const online = robotState ? robotState.online : undefined;
    const connecting = robotState
      ? (robotState.xarm_is_connecting ?? info.xarm_is_connecting)
      : undefined;

    const snapshot = {
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

    traceBoot("BOOT_SNAPSHOT_COLLECTED", summarizePayload(snapshot));
    return snapshot;
  }

  function isReadySnapshot(snapshot) {
    if (!snapshot) {
      return false;
    }
    const hasIdentity = Number.isFinite(snapshot.axis) && Number.isFinite(snapshot.type);
    const hasJoints = Array.isArray(snapshot.joints) && snapshot.joints.length > 0;
    const hasMount = Array.isArray(snapshot.mountDegrees) && snapshot.mountDegrees.length >= 2;
    const connected = snapshot.connected !== false;
    const online = snapshot.online !== false;
    const connecting = snapshot.connecting === true;
    return hasIdentity && hasJoints && hasMount && connected && online && !connecting;
  }

  function postToViewer(type, payload) {
    const viewerWindow = getViewerWindow();
    if (!viewerWindow) {
      traceBoot("BOOT_TX_SKIPPED_NO_VIEWER", { type });
      return;
    }
    messageSeq += 1;
    traceBoot("BOOT_TX", { seq: messageSeq, type, payload: summarizePayload(payload) });
    viewerWindow.postMessage({ type, payload }, window.location.origin);
  }

  function pushInitWithSnapshot(snapshot) {
    const current = snapshot || collectArmSnapshot();
    postToViewer("arm3d:init", {
      axis: current.axis,
      type: current.type,
      mountDegrees: current.mountDegrees || [0, 0],
      endEffector: current.endEffector,
    });
    postToViewer("arm3d:config", { fallbackPolling: false, ignoreMount: false });
  }

  function pushBootstrapWithSnapshot(snapshot) {
    const current = snapshot || collectArmSnapshot();
    bootstrapSeq += 1;
    bootstrapAcked = false;
    setContainerState("data-arm3d-bootstrap", "pending");
    traceBoot("BOOT_BOOTSTRAP_SEND", {
      seq: bootstrapSeq,
      joints: Array.isArray(current.joints) ? current.joints.length : 0,
      mount: current.mountDegrees,
    });
    postToViewer("arm3d:bootstrap", {
      axis: current.axis,
      type: current.type,
      joints: current.joints,
      mountDegrees: current.mountDegrees || [0, 0],
      lift: current.lift,
      timestamp: current.timestamp,
      endEffector: current.endEffector,
      seq: bootstrapSeq,
    });
    postToViewer("arm3d:config", { fallbackPolling: false, ignoreMount: false });
  }

  function pushStateWithSnapshot(snapshot) {
    const current = snapshot || collectArmSnapshot();
    const payload = {
      joints: current.joints,
      lift: current.lift,
      timestamp: current.timestamp,
    };
    if (Array.isArray(current.mountDegrees) && current.mountDegrees.length >= 2) {
      payload.mountDegrees = current.mountDegrees;
    }
    postToViewer("arm3d:state", payload);
  }

  function maybeOpenGateAndPush(snapshot) {
    if (gateOpened) {
      return;
    }
    if (!isReadySnapshot(snapshot)) {
      traceBoot("BOOT_GATE_WAIT", {
        viewerReady,
        joints: Array.isArray(snapshot && snapshot.joints) ? snapshot.joints.length : 0,
        mount: snapshot && snapshot.mountDegrees,
        connected: snapshot && snapshot.connected,
        online: snapshot && snapshot.online,
        connecting: snapshot && snapshot.connecting,
      });
      setContainerState("data-arm3d-bootstrap", "waiting-gate");
      return;
    }
    gateOpened = true;
    traceBoot("BOOT_GATE_OPEN", {
      axis: snapshot && snapshot.axis,
      type: snapshot && snapshot.type,
      joints: Array.isArray(snapshot && snapshot.joints) ? snapshot.joints.length : 0,
      mount: snapshot && snapshot.mountDegrees,
    });
    setContainerState("data-arm3d-bootstrap", "gate-open");
    if (USE_V2) {
      pushBootstrapWithSnapshot(snapshot);
    } else {
      pushInitWithSnapshot(snapshot);
      pushStateWithSnapshot(snapshot);
    }
  }

  function ensurePushLoop() {
    if (pushTimer) {
      return;
    }
    pushTimer = window.setInterval(() => {
      if (!viewerReady) {
        setContainerState("data-arm3d-bootstrap", "waiting-ready");
        return;
      }
      const snapshot = collectArmSnapshot();
      maybeOpenGateAndPush(snapshot);
      if (!gateOpened) {
        return;
      }
      if (USE_V2 && !bootstrapAcked) {
        traceBoot("BOOT_BOOTSTRAP_RETRY", { seq: bootstrapSeq + 1 });
        pushBootstrapWithSnapshot(snapshot);
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
      setContainerState("data-arm3d-bootstrap", "iframe-created");
      traceBoot("BOOT_IFRAME_CREATED", { src: IFRAME_SRC, useV2: USE_V2 });
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
    window.__arm3dDumpBootLog = function () {
      const list = Array.isArray(window.__arm3dBootLog) ? window.__arm3dBootLog : [];
      console.log("[ARM3D_HOST] BOOT_LOG_DUMP", list);
      return list;
    };

    traceBoot("BOOT_HOST_START", { useV2: USE_V2, intervalMs: PUSH_INTERVAL_MS });
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

      const incomingType = event.data && event.data.type;
      if (typeof incomingType === "string" && incomingType.indexOf("arm3d:") === 0) {
        messageSeq += 1;
        traceBoot("BOOT_RX", {
          seq: messageSeq,
          type: incomingType,
          payload: summarizePayload(event.data.payload),
        });
      }

      if (event.data.type === "arm3d:ready") {
        viewerReady = true;
        const container = document.querySelector(CONTAINER_SELECTOR);
        if (container) {
          container.setAttribute("data-arm3d-iframe", "ready");
        }
        traceBoot("BOOT_VIEWER_READY");
        setContainerState("data-arm3d-bootstrap", "viewer-ready");
        const snapshot = collectArmSnapshot();
        maybeOpenGateAndPush(snapshot);
      }

      if (event.data.type === "arm3d:bootstrapAck") {
        const ackSeq = Number(event.data && event.data.payload && event.data.payload.seq);
        if (Number.isFinite(ackSeq) && ackSeq === bootstrapSeq) {
          bootstrapAcked = true;
          traceBoot("BOOT_BOOTSTRAP_ACK", { seq: ackSeq });
          const container = document.querySelector(CONTAINER_SELECTOR);
          if (container) {
            container.setAttribute("data-arm3d-bootstrap", "acked");
          }
        }
      }

      if (event.data.type === "arm3d:requestSnapshot") {
        viewerReady = true;
        traceBoot("BOOT_SNAPSHOT_REQUESTED");
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
