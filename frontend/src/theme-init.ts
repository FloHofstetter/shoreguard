/** FOUC guard: apply the persisted theme before first paint. */

document.documentElement.setAttribute(
  "data-bs-theme",
  localStorage.getItem("sg-theme") || "dark",
);
