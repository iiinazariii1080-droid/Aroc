(function () {
  const VIEWER_STYLE_ID = "arm3d-isolated-viewer-style";

  function injectIsolationStyles() {
    if (document.getElementById(VIEWER_STYLE_ID)) {
      return;
    }

    const style = document.createElement("style");
    style.id = VIEWER_STYLE_ID;
    style.textContent = `
      html, body, #app { width: 100% !important; height: 100% !important; overflow: hidden !important; }
      body > :not(#app) { display: none !important; }

      .float-arm {
        position: fixed !important;
        inset: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        transform: none !important;
        z-index: 1 !important;
      }

      .float-arm > :not(.xarm-container) { display: none !important; }
      .xarm-container {
        width: 100% !important;
        height: 100% !important;
        max-width: none !important;
        max-height: none !important;
      }

      .xarm-container > :not(.arm3d) { display: none !important; }
      .arm3d,
      .arm3d > div,
      .arm3d canvas {
        width: 100% !important;
        height: 100% !important;
        max-width: none !important;
        max-height: none !important;
      }

      iframe[id^="camera"],
      .header,
      .arm-intro,
      .btn-show {
        display: none !important;
      }
    `;

    document.head.appendChild(style);
  }

  function activateWhenReady() {
    injectIsolationStyles();

    const armContainer = document.querySelector(".float-arm .xarm-container .arm3d");
    if (!armContainer) {
      return false;
    }

    document.body.setAttribute("data-arm3d-isolated", "1");

    try {
      window.parent.postMessage({ type: "arm3d_viewer_ready" }, "*");
    } catch (error) {
      console.warn("arm3d viewer postMessage failed", error);
    }

    return true;
  }

  function boot() {
    if (activateWhenReady()) {
      return;
    }

    const intervalId = window.setInterval(function () {
      if (activateWhenReady()) {
        window.clearInterval(intervalId);
      }
    }, 250);

    window.setTimeout(function () {
      window.clearInterval(intervalId);
    }, 20000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
