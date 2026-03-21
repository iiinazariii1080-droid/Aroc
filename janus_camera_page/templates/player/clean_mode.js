// Clean mode: hide UI controls by default; ?controls shows them.
// Extracted from inline <script> for CSP compliance (no unsafe-inline).
(function () {
  'use strict';
  if (!new URLSearchParams(window.location.search).has('controls')) {
    var s = document.createElement('style');
    s.id = 'clean-mode';
    var nonceMeta = document.querySelector('meta[name="style-nonce"]');
    if (nonceMeta && nonceMeta.content) s.nonce = nonceMeta.content;
    s.textContent = '.controls, #statusPill, #statsBox, #debugPanel { visibility: hidden !important; opacity: 0 !important; pointer-events: none !important; position: fixed !important; width: 0 !important; height: 0 !important; overflow: hidden !important; }';
    document.head.appendChild(s);
  }
})();
