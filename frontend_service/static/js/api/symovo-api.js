class SymovoApi extends ApiConnector {
  constructor(baseUrl = "/api/v1/symovo") {
    super(baseUrl);
  }

  getStatus() { return this._get("/status"); }
  healthz() { return this._get("/healthz"); }
  readyz() { return this._get("/readyz"); }

  getTransport(id) { return this._get(`/transport/${id}`); }
  waitForTransportChanges(id) { return this._get(`/transport/${id}/wait_for_changes`); }

  getMap() { return this._get("/map"); }
  getPose() { return this._get("/pose"); }
  getChargingStation() { return this._get("/charging_stations"); }
  faultReset() { return this._get("/fault_reset"); }

  goToPose(payload) { return this._post("/go_to_pose", payload); }
  checkPose(payload) { return this._post("/check_pose", payload); }

//   /api/v1/symovo/go_to_charging_station/{station_id}
// station_id: int

  goToChargingStation(station_id) { return this._post(`/go_to_charging_station/${station_id}`, {}); } // Activates a charging station by its ID and lets the AGV navigate there automatically.
}
