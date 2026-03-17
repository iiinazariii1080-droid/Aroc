# Janus camera page

Web player for RealSense/Janus streaming. Uses the AutonomousPlayer stack (see `templates/player/README.md`).

## Janus JavaScript library (required)

The backend serves **janus.js** from `templates/janus.js`. This file is **not** included in the repo (it is the official Janus Gateway browser library).

If you see:

- **404** on `/api/v1/.../janus.js`, or  
- **Janus is not defined** in the console,

add the library to the project:

1. Copy the Janus JavaScript file into `templates/janus.js`, for example:
   - From [janus-gateway](https://github.com/meetecho/janus-gateway): use the `janus.js` from the `npm` folder or from the HTML demo assets.
   - Or install the `janus-gateway` npm package and copy the bundled `janus.js` into `templates/`.

2. Restart or run the app so that `templates/janus.js` exists when the route `/api/v1/{CAM_TYPE}/janus.js` is requested.

Without this file, the player cannot connect to the Janus WebRTC gateway.
