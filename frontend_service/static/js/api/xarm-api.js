class XarmApi extends ApiConnector {
  constructor(baseUrl = "/api/v1/xarm") {
    super(baseUrl);
  }

  change_joints(payload) { return this._post("/move/change_joints", payload); }
  change_pose(payload) { return this._post("/move/change_pose", payload); }
  change_tool_position(payload) { return this._post("/move/change_tool_position", payload); }
  complex_move_with_joints_dict(payload) { return this._post("/complex_move/with_joints_dict", payload); }

  gripper_take() { return this._post("/gripper/take"); }
  gripper_drop() { return this._post("/gripper/drop"); }

  recover() { return this._post("/recover"); }
  enable_motion() { return this._post("/enable_motion"); }
  disable_motion() { return this._post("/disable_motion"); }
  fault_reset() { return this.recover(); }
  get_current_position() { return this._get("/current_position"); }
  get_joints_position() { return this._get("/joints_position"); }
  get_status() { return this._get("/status"); }

  healthz() { return fetch("/healthz").then(r => r.json()); }
  readyz() { return fetch("/readyz").then(r => r.json()); }
}