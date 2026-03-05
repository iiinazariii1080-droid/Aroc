/**
 * binary-parsers.ts — Shared base64 / array parsing utilities.
 *
 * Centralizes the base64 → typed array conversion used by both
 * DepthApi (data layer) and Orchestrator (domain layer).
 */

/**
 * Parse depth data (base64 string or number[]) into Uint16Array.
 *
 * @param data - Base64 string or numeric array.
 * @param expectedLen - Expected number of uint16 elements (used as fallback size).
 */
export function parseBase64Uint16(data: string | number[], expectedLen: number): Uint16Array {
  if (Array.isArray(data)) return new Uint16Array(data);
  if (typeof data === 'string') {
    const binary = atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Uint16Array(bytes.buffer);
  }
  return new Uint16Array(expectedLen);
}

/**
 * Parse RGB data (base64 string or number[]) into Uint8Array.
 *
 * @param data - Base64 string or numeric array.
 * @param expectedLen - Expected number of uint8 elements (used as fallback size).
 */
export function parseBase64Uint8(data: string | number[], expectedLen: number): Uint8Array {
  if (Array.isArray(data)) return new Uint8Array(data);
  if (typeof data === 'string') {
    const binary = atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
  return new Uint8Array(expectedLen);
}
