# Joystick layout (current configuration)

Button and axis indices are zero-based. Frames use `axes[]` and `buttons[]` arrays (lengths extended to at least 6 axes and 18 buttons where needed).

---

## 1. Buttons — manipulator (xArm)

| Button | Action | Condition | Effect |
|--------|--------|-----------|--------|
| **0** | Gripper toggle | Edge 0→1 (press) | Open/close gripper (take/drop). |
| **9** | Z decrease | Press or hold | Arm move: position Z down. Single tap = one step; hold = continuous. |
| **11** | Z increase | Press or hold | Arm move: position Z up. |
| **12** | X increase | Press or hold | Arm move: position X forward. |
| **14** | X decrease | Press or hold | Arm move: position X backward. |
| **13** | Y increase | Press or hold | Arm move: position Y forward. |
| **15** | Y decrease | Press or hold | Arm move: position Y backward. |
| **3** | Autotake | Edge 0→1 (press) | Run autotake sequence. |

---

## 2. Buttons + axes — orientation (attitude)

| Input | Condition | Effect |
|-------|-----------|--------|
| **Button 2** held + **Axis 2** | `buttons[2]==1` and axis 2 outside deadzone | Yaw: increase or decrease depending on axis sign. |
| **Axis 2** only | No button 2, axis 2 outside deadzone | Roll: increase or decrease. |
| **Axis 3** only | No button 2, axis 3 outside deadzone (or stronger than axis 2) | Pitch: increase or decrease. |

---

## 3. Buttons — AGV teleop (nav2adapter)

Mapped like W/A/S/D. Commands are sent to the main API  
`http://<SYMOVO_NAV2ADAPTER_HOST>:7905/api/v1/robots/<SYMOVO_ROBOT_ID>/move/speed`  
with JSON `{ "speed", "angular_speed", "duration" }`.  
Speed and angular magnitude come from `SYMOVO_TELEOP_LINEAR` and `SYMOVO_TELEOP_ANGULAR` (configurable via API).

| Button | Key | Effect | Request shape |
|--------|-----|--------|----------------|
| **4** | W | Forward | `speed > 0`, `angular_speed == 0` |
| **6** | S | Backward | `speed < 0`, `angular_speed == 0` |
| **7** | A | Turn left | `speed == 0`, `angular_speed > 0` |
| **5** | D | Turn right | `speed == 0`, `angular_speed < 0` |

Combinations (e.g. 4+7) add contributions: forward + left uses both linear and angular in one request.  
When all of 4, 5, 6, 7 are released, a stop command (`speed: 0`, `angular_speed: 0`) is sent.

---

## 4. Stop / release

| Condition | Effect |
|-----------|--------|
| All buttons released (no arm, no teleop) | Manipulator: `MOVE_STOP` (arm step over / stop). |
| All teleop buttons (4,5,6,7) released after being pressed | AGV: stop command to move/speed. |

---

## 5. Priority order (in interpreter)

1. Button 0 edge → gripper toggle.  
2. Buttons 9, 11, 12, 13, 14, 15: edge → single step; hold → continuous arm move.  
3. Button 3 edge → autotake.  
4. Button 2 + axis 2 → yaw; else axes 2/3 → roll/pitch.  
5. Buttons 4, 5, 6, 7 → AGV teleop (composed speed/angular_speed); all released → AGV stop.  
6. No buttons → manipulator stop.

---

## 6. Config and API

- **AGV teleop base:** `SYMOVO_NAV2ADAPTER_HOST` (e.g. `192.168.1.10`), port 7905.  
- **Robot id:** `SYMOVO_ROBOT_ID` (e.g. `fahrdummy-01`).  
- **Teleop params:** duration, linear speed (m/s), angular speed (rad/s) — get/set via  
  `GET /symovo_teleop_config` and `PUT /symovo_teleop_config`.  
- **Drive mode:** enable/disable via `PUT /symovo_drive_mode` with body `{"enable": true|false}`  
  (proxied to `PUT <nav2adapter_host>:7905/drive_mode?enable=...`).

---

## 7. Summary table

| Button | Action |
|--------|--------|
| 0 | Gripper toggle |
| 3 | Autotake |
| 4 | AGV forward (W) |
| 5 | AGV right (D) |
| 6 | AGV backward (S) |
| 7 | AGV left (A) |
| 9 | Arm Z down |
| 11 | Arm Z up |
| 12 | Arm X forward |
| 13 | Arm Y forward |
| 14 | Arm X backward |
| 15 | Arm Y backward |
| 2 + axis 2 | Attitude yaw |
| Axes 2/3 (no btn 2) | Attitude roll / pitch |
