# Workspace calibration

Workspace box (WS): 40×90×120 cm. All values in mm.

## Default config (axes aligned)

- `WS.size = (400, 900, 1200)` mm  
- `base_in_ws = (150, 450, 0)` mm  
- `margin = 40` mm  
- Rotation: identity (WS axes parallel to base)

Boundary in base frame: `x ∈ [-110, 210]`, `y ∈ [-410, 410]`, `z ∈ [40, 1160]` (with margin).

## Calibration steps (if rotation or origin differ)

1. Choose physical WS origin (e.g. left-front-bottom corner) and mark it.
2. Measure three points: origin, origin + X direction, origin + Y direction.
3. Compute `T_ws_base` (translation + rotation). If axes are aligned, rotation is identity and translation = base position in WS.
4. Store in config (`app/config.py` or `config/workspace.yaml`) and version.

## Hardware boundary

On connect, `apply_boundary_to_arm(arm)` sets reduced TCP boundary and enables reduced mode. See `drivers/xarm_driver/safety/boundary.py`.
