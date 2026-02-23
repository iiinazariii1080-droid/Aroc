// client.js
let pc = null;
let dataChannel = null;
let depthLabel = null;
let depthTimeout = null;

// Функция для определения базового URL
function getBaseUrl() {
  // Проверяем, работаем ли мы через прокси
  if (window.location.pathname.startsWith('/api/v1/depth_camera')) {
    return '/api/v1/depth_camera';
  }
  return '';
}

document.addEventListener('DOMContentLoaded', () => {
  depthLabel = document.getElementById('depth-label');
});

/**
 * Initialize WebRTC connection and start overlay.
 */
async function setOverlay(colorIndex = 0, stereoIndex = 0) {
  cleanup();

  pc = new RTCPeerConnection({ sdpSemantics: 'unified-plan' });

  // Get tracks
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });

  // Get media
  pc.addEventListener('track', (evt) => {
    const stream = evt.streams[0];
    if (evt.track.kind === 'video') {
      document.getElementById('video').srcObject = stream;
    } else if (evt.track.kind === 'audio') {
      document.getElementById('audio').srcObject = stream;
    }
  });

  // DataChannel
  dataChannel = pc.createDataChannel('control', { ordered: true });

  dataChannel.addEventListener('open', () => {
    console.log('DataChannel opened');
  });

  dataChannel.addEventListener('message', handleDataMessage);

  try {
    const baseUrl = getBaseUrl();
    await negotiate(`${baseUrl}/overlay_offer`, 'overlay', colorIndex, stereoIndex);
  } catch (err) {
    console.error('Negotiation failed:', err);
    alert('Connection error: ' + err.message);
  }
}

/**
 * Send pixel coordinates via DataChannel.
 */
function sendPixelPercent(xPercent, yPercent) {
  if (dataChannel && dataChannel.readyState === 'open') {
    const msg = { type: 'pixel', x: xPercent, y: yPercent };
    dataChannel.send(JSON.stringify(msg));
  } else {
    console.warn('DataChannel is not open');
  }
}

/**
 * Handle incoming DataChannel messages.
 */
function handleDataMessage(event) {
  try {
    const msg = JSON.parse(event.data);
    if (msg.type === 'depth') {
      const text = msg.depth > 0
        ? `${(msg.depth / 1000).toFixed(2)}m`
        : '--';

      if (depthLabel) {
        depthLabel.innerText = text;
        depthLabel.classList.add('show');
        clearTimeout(depthTimeout);
        // Keep depth visible until new measurement
      }
    }
  } catch (err) {
    console.warn('Invalid message:', event.data);
  }
}

/**
 * Signaling: send SDP offer and get answer.
 */
async function negotiate(url, mode, colorIndex, stereoIndex) {
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  await waitForIceGathering();

  const body = {
    sdp: pc.localDescription.sdp,
    type: pc.localDescription.type,
    color_index: colorIndex,
    stereo_index: stereoIndex,
    mode: mode
  };

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    throw new Error(`Server error: ${response.status}`);
  }

  const answer = await response.json();
  await pc.setRemoteDescription(answer);
}

/**
 * Wait for ICE gathering to complete.
 */
function waitForIceGathering() {
  return new Promise((resolve) => {
    if (pc.iceGatheringState === 'complete') {
      resolve();
    } else {
      const checkState = () => {
        if (pc.iceGatheringState === 'complete') {
          pc.removeEventListener('icegatheringstatechange', checkState);
          resolve();
        }
      };
      pc.addEventListener('icegatheringstatechange', checkState);
    }
  });
}

/**
 * Clean up resources and close connection.
 */
function cleanup() {
  if (depthTimeout) {
    clearTimeout(depthTimeout);
    depthTimeout = null;
  }

  if (pc) {
    pc.getSenders().forEach((sender) => {
      if (sender.track) sender.track.stop();
    });
    pc.close();
    pc = null;
  }

  dataChannel = null;

  if (depthLabel) {
    depthLabel.classList.remove('show');
    depthLabel.innerText = '';
  }
}
// ... your existing code above ...

/** * Handle incoming DataChannel messages. */
function handleDataMessage(event) {
  try {
    const msg = JSON.parse(event.data);
    if (msg.type === 'depth') {
      const text = msg.depth > 0 ? `${(msg.depth / 1000).toFixed(2)}m` : '--';
      if (depthLabel) {
        depthLabel.innerText = text;
        depthLabel.classList.add('show');
        clearTimeout(depthTimeout);
        // Keep depth visible until new measurement
      }

      // === ADDED: pass to UI ===
      if (window.UI && typeof window.UI.onDepthMessage === 'function') {
        window.UI.onDepthMessage(msg);
      }
      // ================================
    }
  } catch (err) {
    console.warn('Invalid message:', event.data);
  }
}

// ... rest of your code unchanged ...
