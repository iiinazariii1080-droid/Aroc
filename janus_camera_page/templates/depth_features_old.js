(() => {
  "use strict";

  const body = document.body;
  if (!body) {
    console.warn("[depth] document.body missing");
    return;
  }

  const dataset = body.dataset || {};
  const PROTOCOL = window.location.protocol;
  const HOST = window.location.host;
  const HOSTNAME = window.location.hostname;
  const PORT = window.location.port;

  function joinUrl(base, suffix) {
    if (!suffix) return base;
    const trimmedBase = base.replace(/\/+$/, "");
    const trimmedSuffix = suffix.replace(/^\/+/, "");
    if (!trimmedSuffix) return trimmedBase;
    return `${trimmedBase}/${trimmedSuffix}`;
  }

  function applyTemplate(value) {
    if (!value) return value;
    return value.replace(
      /\{(hostname|host|protocol|origin)\}/gi,
      (_, token) => {
        switch (token.toLowerCase()) {
          case "hostname":
            return HOSTNAME;
          case "host":
            return HOST;
          case "protocol":
            return PROTOCOL.replace(/:$/, "");
          case "origin":
            return window.location.origin;
          default:
            return _;
        }
      }
    );
  }

  const apiPrefixMode = (dataset.apiPrefixMode || "port").toLowerCase();
  const apiPrefix = dataset.apiPrefix || "/api/v1/__CAM_TYPE__";
  const restBaseOverride = dataset.restBase ? applyTemplate(dataset.restBase) : null;
  let restBase = restBaseOverride || `${PROTOCOL}//${HOST}`;
  const shouldApplyPrefix =
    apiPrefixMode === "always" || (apiPrefixMode === "port" && PORT !== "8900");
  if (!restBaseOverride && shouldApplyPrefix) {
    restBase = joinUrl(restBase, apiPrefix);
  }

  const depthHud =
    dataset.depthHud && document.querySelector(dataset.depthHud);
  const depthToast =
    dataset.depthToast && document.querySelector(dataset.depthToast);
  const depthClickTarget =
    dataset.depthClick && document.querySelector(dataset.depthClick);
  const probeButton =
    dataset.depthProbeBtnId && document.getElementById(dataset.depthProbeBtnId);

  const defaultProbePoint = {
    x: clampPercentValue(dataset.reticleX, 50),
    y: clampPercentValue(dataset.reticleY, 50),
  };

  const depthEndpointRaw = dataset.depthEndpoint || null;
  const depthEndpoint = depthEndpointRaw
    ? depthEndpointRaw.startsWith("http")
      ? depthEndpointRaw
      : joinUrl(restBase, depthEndpointRaw)
    : null;

  const videoEl = document.getElementById(dataset.videoId || "video");

  function clampPercentValue(value, fallback) {
    const num = parseFloat(value);
    if (Number.isFinite(num)) {
      return Math.min(100, Math.max(0, num));
    }
    return fallback;
  }

  function createDepthProbeFeature(defaultPoint = { x: 50, y: 50 }) {
    if (!depthEndpoint) {
      console.warn("[DEPTH] depthEndpoint not configured; probe disabled");
      return null;
    }
    if (!videoEl) return null;

    let toastTimer = null;

    function showToast(text) {
      if (!depthToast) return;
      depthToast.textContent = text;
      depthToast.style.display = "block";
      depthToast.style.opacity = "1";
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        depthToast.style.opacity = "0";
        setTimeout(() => {
          depthToast.style.display = "none";
        }, 250);
      }, 1500);
    }

    function clampPercent(value) {
      return Math.min(100, Math.max(0, value));
    }

    function parse2dMatrix(transform) {
      if (!transform || transform === "none") {
        return null;
      }
      if (typeof DOMMatrixReadOnly !== "undefined") {
        try {
          const m = new DOMMatrixReadOnly(transform);
          const a = Number.isFinite(m.a) ? m.a : m.m11;
          const b = Number.isFinite(m.b) ? m.b : m.m12;
          const c = Number.isFinite(m.c) ? m.c : m.m21;
          const d = Number.isFinite(m.d) ? m.d : m.m22;
          if ([a, b, c, d].every((val) => Number.isFinite(val))) {
            return { a, b, c, d };
          }
        } catch (err) {
          // ignore
        }
      }
      const match = transform.match(/^matrix\(([^)]+)\)$/);
      if (!match) return null;
      const parts = match[1].split(",").map((part) => parseFloat(part.trim()));
      if (parts.length < 4 || parts.slice(0, 4).some((val) => !Number.isFinite(val))) {
        return null;
      }
      return { a: parts[0], b: parts[1], c: parts[2], d: parts[3] };
    }

    function getVideoRotationDegrees() {
      if (!videoEl || typeof window === "undefined" || !window.getComputedStyle) {
        return 0;
      }
      try {
        const transform = window.getComputedStyle(videoEl).transform;
        const matrix = parse2dMatrix(transform);
        if (!matrix) return 0;
        const rawAngle = Math.atan2(matrix.b, matrix.a);
        if (!Number.isFinite(rawAngle)) return 0;
        const deg = (rawAngle * 180) / Math.PI;
        const normalized = ((deg % 360) + 360) % 360;
        const snapped = Math.round(normalized / 90) * 90;
        return snapped === 360 ? 0 : snapped;
      } catch (err) {
        return 0;
      }
    }

    function adjustForVideoRotation(xNorm, yNorm, angleDeg) {
      if (!Number.isFinite(angleDeg) || angleDeg % 360 === 0) {
        return { x: xNorm, y: yNorm };
      }
      const rad = (-angleDeg * Math.PI) / 180;
      const cosA = Math.cos(rad);
      const sinA = Math.sin(rad);
      const cx = xNorm - 0.5;
      const cy = yNorm - 0.5;
      const rx = cx * cosA - cy * sinA;
      const ry = cx * sinA + cy * cosA;
      return {
        x: Math.min(1, Math.max(0, rx + 0.5)),
        y: Math.min(1, Math.max(0, ry + 0.5)),
      };
    }

    function pointerToDisplayPercent(point) {
      const rect = videoEl.getBoundingClientRect();
      const width = rect.width || window.innerWidth || 1;
      const height = rect.height || window.innerHeight || 1;
      const x = clampPercent(((point.clientX - rect.left) / width) * 100);
      const yTop = (point.clientY - rect.top) / height;
      const y = clampPercent((1 - yTop) * 100);
      return { x, y };
    }

    function displayToSamplePercent(xPct, yPct) {
      const domX = clampPercent(xPct);
      const domY = clampPercent(100 - yPct);
      const rotationDeg = getVideoRotationDegrees();
      const adjusted = adjustForVideoRotation(domX / 100, domY / 100, rotationDeg);
      return {
        x: clampPercent(adjusted.x * 100),
        y: clampPercent(adjusted.y * 100),
      };
    }

    async function probeDepthAt(displayXPct, displayYPct) {
      if (!Number.isFinite(displayXPct) || !Number.isFinite(displayYPct)) return;
      const sample = displayToSamplePercent(displayXPct, displayYPct);
      const params = new URLSearchParams();
      params.set("x", Number(sample.x).toFixed(6));
      params.set("y", Number(sample.y).toFixed(6));
      params.set("message", JSON.stringify({ x: sample.x, y: sample.y }));
      const url = `${depthEndpoint}?${params.toString()}`;
      const started = performance.now();
      try {
        const resp = await fetch(url, { cache: "no-store" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const depthValue = Number.isFinite(data.depth)
          ? `${data.depth.toFixed(3)} m`
          : "NaN";
        if (depthHud) {
          depthHud.innerHTML = `x: ${displayXPct.toFixed(1)}, y: ${displayYPct.toFixed(1)}<br>depth: ${depthValue}`;
        }
        const latency = Math.round(performance.now() - started);
        showToast(`depth: ${depthValue} (${latency} ms)`);
      } catch (err) {
        if (depthHud) {
          depthHud.innerHTML = `x: ${displayXPct.toFixed(1)}, y: ${displayYPct.toFixed(1)}<br>depth: error`;
        }
        showToast("depth: error");
        console.error("[DEPTH] probe error:", err);
      }
    }

    function handleDepthPointer(point) {
      const coords = pointerToDisplayPercent(point);
      if (!coords) return;
      probeDepthAt(coords.x, coords.y);
    }

    function attach() {
      const target = depthClickTarget || videoEl;
      if (!target) return;
      target.addEventListener("click", (evt) => handleDepthPointer(evt));
      target.addEventListener(
        "touchstart",
        (evt) => {
          const touch = evt.touches && evt.touches[0];
          if (!touch) return;
          handleDepthPointer(touch);
        },
        { passive: true }
      );
    }

    return {
      attach,
      probeAt: (x, y) => probeDepthAt(x, y),
      probeCenter: () => probeDepthAt(defaultPoint.x, defaultPoint.y),
    };
  }

  function registerDepthProbeControls(depthProbe) {
    if (!depthProbe) return;
    let reticleHotkeyBound = false;
    if (probeButton && !probeButton.hasAttribute("data-probe-bound")) {
      const idleLabel = probeButton.textContent || "Measure depth";
      const handleProbe = async () => {
        probeButton.disabled = true;
        probeButton.textContent = "Sampling…";
        try {
          await depthProbe.probeCenter();
        } catch (err) {
          console.warn("[DEPTH] probe via button failed:", err);
        } finally {
          probeButton.disabled = false;
          probeButton.textContent = idleLabel;
        }
      };
      probeButton.setAttribute("data-probe-bound", "1");
      probeButton.addEventListener("click", handleProbe);
    }

    if (!reticleHotkeyBound) {
      window.addEventListener("keydown", (evt) => {
        if (evt.code === "Space" && !evt.repeat) {
          depthProbe?.probeCenter();
          evt.preventDefault();
        }
      });
      reticleHotkeyBound = true;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const depthProbe = createDepthProbeFeature(defaultProbePoint);
    depthProbe?.attach();
    registerDepthProbeControls(depthProbe);
  });
})();

