(function(){
  'use strict';

  // Compatibility shim: loads the player/ stack and boots it.
  // Prefer including player/ scripts directly (see color_view.html, color_view_v2.html).
  // This shim allows a single <script src="streamer.js"> to work where needed.

  if (window.AutonomousPlayer && window.AutonomousPlayer.App && window.autonomousPlayerController) {
    return;
  }

  function currentScriptBase(){
    const el = document.currentScript;
    const src = el && el.src ? el.src : '';
    // .../streamer.js -> .../
    return src.replace(/streamer\.js(\?.*)?$/, '');
  }

  function loadScriptOnce(src){
    return new Promise((resolve, reject) => {
      // skip if already present
      const existing = Array.from(document.scripts).find(s => s.src === src);
      if (existing) {
        resolve(true);
        return;
      }
      const s = document.createElement('script');
      s.src = src;
      s.async = false;
      s.onload = () => resolve(true);
      s.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(s);
    });
  }

  async function boot(){
    const base = currentScriptBase();
    const paths = [
      'player/ns.js',
      'player/config.js',
      'player/core/player_state.js',
      'player/core/codes.js',
      'player/core/domain_events.js',
      'player/core/connection_policy.js',
      'player/core/state_machine_canonical.js',
      'player/core/invariants.js',
      'player/core/fail_closed.js',
      'player/core/backoff.js',
      'player/core/recovery_policy.js',
      'player/ports/clock_port.js',
      'player/ports/logger_port.js',
      'player/ports/streaming_port.js',
      'player/ports/video_port.js',
      'player/adapters/clock.js',
      'player/adapters/logger.js',
      'player/adapters/janus_session_manager.js',
      'player/adapters/dom_ui_adapter.js',
      'player/adapters/janus_streaming_adapter.js',
      'player/adapters/janus_textroom_adapter.js',
      'player/app/stats_service.js',
      'player/app/joystick_service.js',
      'player/app/recovery_map.js',
      'player/app/reconnect_coordinator.js',
      'player/app/timer_coordinator.js',
      'player/app/watchdog_service.js',
      'player/app/player_controller.js',
      'player/bootstrap.js',
    ];

    for (const p of paths) {
      await loadScriptOnce(base + p);
    }
  }

  boot().catch((e) => {
    console.error('[AutonomousPlayer] failed to load clean player', e);
  });
})();
