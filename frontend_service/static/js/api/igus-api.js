class IgusApi extends ApiConnector {
    constructor(baseUrl = "/api/v1/igus") {
      super(baseUrl);
      this.cachedPosition = 0;
    }
  
    async status() {
      const data = await this._get("/status");
      if (!data.error) this.cachedPosition = data.position ?? this.cachedPosition;
      return data;
    }

    async fetchPosition() {
      const data = await this._get("/position");
      if (data.position !== undefined) this.cachedPosition = data.position;
      return this.cachedPosition;
    }

    get position() {
      return this.cachedPosition;
    }
  
    motion_status() { return this._get("/is_motion"); }
    reference() { return this._post("/reference"); }
    move_to_position(position, velocity_percent = 50, acceleration_percent = 50) {
      return this._post("/move", { position, velocity_percent, acceleration_percent });
    }
    fault_reset() { return this._post("/fault_reset"); }
  
    healthz() { return fetch("/healthz").then(r => r.json()); }
    readyz() { return fetch("/readyz").then(r => r.json()); }
  }
  