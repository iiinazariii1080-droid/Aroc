class ApiConnector {
  constructor(baseUrl, { timeoutMs = 8000 } = {}) {
    this.baseUrl = baseUrl;
    this.timeoutMs = timeoutMs;
  }

  async _request(method, path, body) {
    const url = `${this.baseUrl}${path}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(url, {
        method,
        headers: {
          "Accept": "application/json",
          ...(method !== "GET"
            ? { "Content-Type": "application/json" }
            : {})
        },
        body: method !== "GET" ? JSON.stringify(body ?? {}) : undefined,
        signal: controller.signal
      });

      let data;
      try {
        data = await response.json();
      } catch (jsonErr) {
        throw new Error(
          response.ok
            ? "Invalid JSON in server response"
            : `Request failed (${response.status})`
        );
      }

      if (!response.ok) {
        const detail = data?.detail;
        const message =
          data?.error ||
          data?.message ||
          (typeof detail === "string" ? detail : detail?.error) ||
          `Request failed (${response.status})`;
        const error = new Error(message);
        error.status = response.status;
        error.payload = data;
        throw error;
      }

      return data;
    } catch (err) {
      if (err.name === "AbortError") {
        throw new Error("Request timeout");
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  _get(path) {
    return this._request("GET", path);
  }

  _post(path, body = {}) {
    return this._request("POST", path, body);
  }
}
