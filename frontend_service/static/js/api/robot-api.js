// RobotApi.js
class RobotApi extends ApiConnector {
  constructor(baseUrl = "/api/v1/robot") {
    super(baseUrl);
  }

  // --------------- Robot commands (return task_id immediately) ---------------
  move_to_box_1(payload) { return this._post('/move/to_box_1', payload); }
  move_to_box_2(payload) { return this._post('/move/to_box_2', payload); }
  move_to_transport_position(payload) { return this._post('/move/to_transport_position', payload); }
  move_to_product(payload) { return this._post('/move/to_product', payload); }
  move_autotake(payload) { return this._post('/move/autotake', payload); }
  move_align(payload) { return this._post('/move/align', payload); } // сервер может не иметь этого роута

  save_trajectory(payload) { return this._post('/autotake_config/trajectory', payload); }
  get_trajectory() { return this._get('/autotake_config/trajectory'); }

  status() { return this._get('/status'); }

  fetch_robot_positions_list() { return this._get('/robot_positions/list'); }
  save_robot_position(payload) { return this._post('/robot_positions/save', payload); }
  run_to_robot_position(position_id) {
    if (!position_id) throw new Error('positionId is required');
    return this._post(`/robot_positions/run?position_id=${encodeURIComponent(position_id)}`, {});
  }
  delete_robot_position(position_id) {
    if (!position_id) throw new Error('positionId is required');
    return this._post(`/robot_positions/delete?position_id=${encodeURIComponent(position_id)}`, {});
  }
  set_ready() { return this._post('/set_ready', {}); }

  healthz() { return fetch('/healthz').then(r => r.json()); }
  readyz() { return fetch('/readyz').then(r => r.json()); }

  // ----------------------------- Task management -----------------------------
  task_status(taskId) {
    if (!taskId) throw new Error('taskId is required');
    return this._get(`/tasks/status/${encodeURIComponent(taskId)}`);
  }
  current_task() {
    return this._get('/tasks/current');
  }
  cancel_task(taskId) {
    if (!taskId) throw new Error('taskId is required');
    return this._post(`/tasks/cancel/${encodeURIComponent(taskId)}`, {});
  }
  cancel_current_task() {
    return this._post('/tasks/cancel_current', {});
  }

  // ----------------------------- High-level helpers --------------------------
  // Waits until task finishes or errors. Returns {status, result}
  async wait_for_task(taskId, {
    intervalMs = 1000,
    timeoutMs = 180000,
    onTick,              // optional (status) => void
  } = {}) {
    if (!taskId) throw new Error('taskId is required');
    const start = Date.now();

    // statuses: pending, working, finished, error, cancelled, not_found
    const isActive = s => s === 'pending' || s === 'working';

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const s = await this.task_status(taskId);
      if (onTick) { try { onTick(s); } catch (_) {} }

      if (!isActive(s.status)) {
        return s; // { status, result }
      }
      if (Date.now() - start > timeoutMs) {
        throw new Error(`Task ${taskId} timed out after ${timeoutMs} ms`);
      }
      await new Promise(r => setTimeout(r, intervalMs));
    }
  }

  // Starts a command that returns { success, task_id }, then optionally waits
  async start_and_maybe_wait(startPromise, {
    wait = true,
    intervalMs = 1000,
    timeoutMs = 180000,
    onTick,
  } = {}) {
    const startResp = await startPromise; // { success, task_id, detail? }
    const taskId = startResp?.task_id;
    if (!taskId) {
      // In case server returned immediate success without async (unlikely now)
      return { status: 'finished', result: startResp ?? null, task_id: null };
    }
    if (!wait) return { status: 'working', result: null, task_id: taskId };
    const final = await this.wait_for_task(taskId, { intervalMs, timeoutMs, onTick });
    return { ...final, task_id: taskId };
  }

  // Convenience wrappers to start and wait for completion in one call:
  move_to_box_1_wait(payload, opts) { return this.start_and_maybe_wait(this.move_to_box_1(payload), opts); }
  move_to_box_2_wait(payload, opts) { return this.start_and_maybe_wait(this.move_to_box_2(payload), opts); }
  move_to_transport_position_wait(payload, opts) { return this.start_and_maybe_wait(this.move_to_transport_position(payload), opts); }
  move_to_product_wait(payload, opts) { return this.start_and_maybe_wait(this.move_to_product(payload), opts); }
  move_autotake_wait(payload, opts) { return this.start_and_maybe_wait(this.move_autotake(payload), opts); }
  run_to_robot_position_wait(position_id, opts) { return this.start_and_maybe_wait(this.run_to_robot_position(position_id), opts); }
}
